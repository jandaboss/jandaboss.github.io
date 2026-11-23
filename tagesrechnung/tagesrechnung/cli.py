"""Kommandozeile: ``python -m tagesrechnung <befehl> [optionen]``"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

from . import __version__, formate
from .ablauf import ausfuehren
from .konfiguration import KonfigurationsFehler, laden
from .rechnungsbuch import Rechnungsbuch
from .versand import SmtpVersand, VersandFehler, probenachricht

log = logging.getLogger("tagesrechnung")

BESCHREIBUNG = """\
Erstellt am Tagesende zu den Bestellungen des Tages je Kunde eine Rechnung (PDF)
und schickt sie dem Kunden per E-Mail.

Beispiele:
  python -m tagesrechnung ausfuehren --probelauf          Vorschau, nichts wird versendet
  python -m tagesrechnung ausfuehren                      Rechnungen für heute erstellen und versenden
  python -m tagesrechnung ausfuehren --nachholen 2        … plus Liegengebliebenes der 2 Vortage (täglicher Lauf)
  python -m tagesrechnung ausfuehren --datum gestern      … für die Bestellungen von gestern
  python -m tagesrechnung liste --offen                   noch nicht zugestellte Rechnungen
  python -m tagesrechnung testmail ich@example.de         SMTP-Einstellungen testen
"""


def _parser() -> argparse.ArgumentParser:
    gemeinsam = argparse.ArgumentParser(add_help=False)
    gemeinsam.add_argument(
        "-c", "--config",
        default=os.environ.get("TAGESRECHNUNG_CONFIG", "config.toml"),
        help="Konfigurationsdatei (Standard: config.toml bzw. Umgebungsvariable TAGESRECHNUNG_CONFIG)",
    )
    gemeinsam.add_argument("-v", "--ausfuehrlich", action="store_true", help="mehr Ausgaben")
    gemeinsam.add_argument("--logdatei", help="Ausgaben zusätzlich in diese Datei schreiben")

    parser = argparse.ArgumentParser(
        prog="tagesrechnung", description=BESCHREIBUNG, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    befehle = parser.add_subparsers(dest="befehl", required=True, metavar="BEFEHL")

    a = befehle.add_parser("ausfuehren", parents=[gemeinsam], help="Rechnungen erstellen und versenden")
    a.add_argument("--datum", default="heute",
                   help="Bestelltag: heute (Standard), gestern, JJJJ-MM-TT oder TT.MM.JJJJ")
    a.add_argument("--nachholen", type=int, default=0, metavar="TAGE",
                   help="zusätzlich noch nicht abgerechnete Bestellungen der TAGE Tage davor abrechnen")
    a.add_argument("--probelauf", action="store_true",
                   help="nichts versenden und nichts festschreiben; PDFs und E-Mails (.eml) in den Probelauf-Ordner")

    l = befehle.add_parser("liste", parents=[gemeinsam], help="erstellte Rechnungen anzeigen")
    l.add_argument("--offen", action="store_true", help="nur noch nicht zugestellte Rechnungen")

    t = befehle.add_parser("testmail", parents=[gemeinsam], help="Test-E-Mail senden, um SMTP zu prüfen")
    t.add_argument("empfaenger", help="E-Mail-Adresse für die Testnachricht")
    return parser


def _logging(ausfuehrlich: bool, logdatei: str | None) -> None:
    handler: list[logging.Handler] = [logging.StreamHandler()]
    if logdatei:
        handler.append(logging.FileHandler(logdatei, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if ausfuehrlich else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handler,
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _logging(args.ausfuehrlich, args.logdatei)
    try:
        konfig = laden(args.config)
    except KonfigurationsFehler as fehler:
        log.error("Konfiguration: %s", fehler)
        return 2

    if args.befehl == "ausfuehren":
        return _ausfuehren(args, konfig)
    if args.befehl == "liste":
        return _liste(args, konfig)
    return _testmail(args, konfig)


def _ausfuehren(args, konfig) -> int:
    heute = datetime.now(konfig.speicher.zeitzone).date()
    try:
        stichtag = formate.datum_lesen(args.datum, heute)
    except ValueError as fehler:
        log.error("%s", fehler)
        return 2
    if args.nachholen < 0:
        log.error("--nachholen darf nicht negativ sein")
        return 2
    if not args.probelauf and not konfig.email.smtp_server:
        log.error("Konfiguration: [email] smtp_server fehlt (für eine Vorschau ohne Versand: --probelauf)")
        return 2

    if args.probelauf:
        log.info("PROBELAUF – es wird nichts versendet und das Rechnungsbuch bleibt unverändert")
    bericht = ausfuehren(konfig, stichtag, nachholen_tage=args.nachholen, probelauf=args.probelauf)

    log.info(
        "Fertig: %d Rechnung(en) erstellt, %d %s, %d Versandfehler, %d Datenfehler",
        len(bericht.erstellt),
        len(bericht.versendet),
        "als .eml gespeichert" if args.probelauf else "versendet",
        len(bericht.fehlgeschlagen),
        len(bericht.datenfehler),
    )
    if args.probelauf:
        log.info("Vorschau liegt in %s", konfig.speicher.probelauf)
    return 0 if bericht.erfolgreich else 1


def _liste(args, konfig) -> int:
    if not konfig.speicher.datenbank.is_file():
        print("Noch keine Rechnungen erstellt.")
        return 0
    buch = Rechnungsbuch.oeffnen(konfig.speicher.datenbank, nur_kopie=True)
    try:
        rechnungen = buch.alle(nur_offene=args.offen)
    finally:
        buch.schliessen()
    if not rechnungen:
        print("Keine offenen Rechnungen." if args.offen else "Noch keine Rechnungen erstellt.")
        return 0
    symbol = konfig.rechnung.waehrungssymbol
    breite = max(len(r.nummer) for r in rechnungen)
    print(f"{'Nummer':<{breite}}  {'Datum':<10}  {'Kunde':<28}  {'Betrag':>13}  Status")
    for r in rechnungen:
        if r.status == "versendet":
            status = f"versendet {r.versendet_am[:16].replace('T', ' ')}"
        else:
            status = f"OFFEN ({r.versuche} Versuch(e))" + (f": {r.letzter_fehler}" if r.letzter_fehler else "")
        print(f"{r.nummer:<{breite}}  {formate.datum(r.rechnungsdatum)}  {r.kunde_name[:28]:<28}  "
              f"{formate.betrag(r.brutto, symbol):>13}  {status}")
    return 0


def _testmail(args, konfig) -> int:
    try:
        nachricht = probenachricht(konfig, args.empfaenger)
        with SmtpVersand(konfig.email) as versand:
            versand.senden(nachricht, "test")
    except VersandFehler as fehler:
        log.error("Testmail fehlgeschlagen: %s", fehler)
        return 1
    log.info("Testmail an %s versendet", args.empfaenger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
