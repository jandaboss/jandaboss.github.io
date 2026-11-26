"""Kommandozeile: ``python -m tagesrechnung <befehl> [optionen]``"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from datetime import datetime, timedelta

from . import __version__, anschriften, ebay, formate
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
  python -m tagesrechnung ausfuehren --nachholen 7        … plus Liegengebliebenes der 7 Vortage (täglicher Lauf)
  python -m tagesrechnung ausfuehren --datum gestern      … für die Bestellungen von gestern
  python -m tagesrechnung liste --offen                   noch nicht zugestellte Rechnungen
  python -m tagesrechnung testmail ich@example.de         SMTP-Einstellungen testen

eBay (einmalig verbinden, dann prüfen):
  python -m tagesrechnung ebay-verbinden
  python -m tagesrechnung ebay-pruefen --tage 14

Rechnung auf eine andere Anschrift (z. B. auf Wunsch des Käufers auf seine Firma):
  python -m tagesrechnung anschrift 12-34567-89012 --firma "Beispiel GmbH" \\
      --strasse "Musterstr. 1" --plz 70191 --ort Stuttgart
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

    v = befehle.add_parser("ebay-verbinden", parents=[gemeinsam],
                           help="Programm einmalig mit Ihrem eBay-Konto verbinden (Bestellungen lesen)")
    v.add_argument("--adresse", help="Adresse nach der eBay-Anmeldung direkt übergeben, statt danach zu fragen")

    a = befehle.add_parser("anschrift", parents=[gemeinsam],
                           help="abweichende Rechnungsanschrift für eine Bestellung eintragen")
    a.add_argument("bestellnummer")
    a.add_argument("--firma", default="", help="Firmenname, z. B. \"Beispiel GmbH\"")
    a.add_argument("--name", help="Ansprechpartner (ohne Angabe bleibt der Name des Käufers stehen)")
    a.add_argument("--strasse", default="")
    a.add_argument("--plz", default="")
    a.add_argument("--ort", default="")
    a.add_argument("--land", default="", help="nur bei Anschriften im Ausland")
    a.add_argument("--ust-idnr", default="", dest="ust_idnr", help="USt-IdNr. des Kunden, falls gewünscht")
    a.add_argument("--wie-bestellt", action="store_true",
                   help="keine andere Anschrift – Bestellung trotz Käuferhinweis normal abrechnen")

    e = befehle.add_parser("ebay-pruefen", parents=[gemeinsam],
                           help="eBay-Bestellungen anzeigen und prüfen, ohne etwas abzurechnen")
    e.add_argument("--tage", type=int, default=7, help="so viele Tage zurückschauen (Standard: 7)")
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
    if args.befehl == "ebay-verbinden":
        return _ebay_verbinden(args, konfig)
    if args.befehl == "ebay-pruefen":
        return _ebay_pruefen(args, konfig)
    if args.befehl == "anschrift":
        return _anschrift(args, konfig)
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


def _anschrift(args, konfig) -> int:
    pfad = konfig.rechnung.anschriften_datei
    if args.wie_bestellt:
        if any((args.firma, args.name, args.strasse, args.plz, args.ort, args.land, args.ust_idnr)):
            log.error("--wie-bestellt bitte ohne weitere Angaben verwenden")
            return 2
        anschrift = anschriften.Anschrift(wie_bestellt=True)
    else:
        anschrift = anschriften.Anschrift(
            firma=args.firma.strip(), name=None if args.name is None else args.name.strip(),
            strasse=args.strasse.strip(), plz=args.plz.strip(), ort=args.ort.strip(),
            land=args.land.strip(), ust_idnr=args.ust_idnr.strip(),
        )
    try:
        anschriften.eintragen(pfad, args.bestellnummer, anschrift)
    except anschriften.AnschriftenFehler as fehler:
        log.error("%s", fehler)
        return 2
    log.info("Bestellung %s: %s (gespeichert in %s)", args.bestellnummer.strip(), anschrift.beschreibung(), pfad)

    if konfig.speicher.datenbank.is_file():
        buch = Rechnungsbuch.oeffnen(konfig.speicher.datenbank, nur_kopie=True)
        try:
            schon = buch.abgerechnet([args.bestellnummer.strip()])
        finally:
            buch.schliessen()
        if schon:
            log.warning("Achtung: Bestellung %s steht bereits auf Rechnung %s. Die Anschrift gilt nur für neue "
                        "Rechnungen – die bestehende Rechnung bitte von Hand korrigieren.",
                        args.bestellnummer.strip(), schon[args.bestellnummer.strip()])
            return 1
    log.info("Die Rechnung wird beim nächsten Lauf erstellt.")
    return 0


def _ebay_einstellungen(konfig):
    if konfig.quelle.typ != "ebay" or konfig.quelle.ebay is None:
        log.error('Konfiguration: Für eBay muss unter [quelle] typ = "ebay" stehen und es braucht einen Abschnitt [ebay]')
        return None
    return konfig.quelle.ebay


def _ebay_verbinden(args, konfig) -> int:
    einst = _ebay_einstellungen(konfig)
    if einst is None:
        return 2
    konto = ebay.EbayKonto(einst)
    state = None
    eingabe = args.adresse
    if not eingabe:
        state = secrets.token_urlsafe(16)
        print("1. Öffnen Sie diese Adresse im Browser und melden Sie sich mit Ihrem eBay-Verkäuferkonto an:\n")
        print(f"   {konto.anmelde_adresse(state)}\n")
        print("2. Erlauben Sie dem Programm, Ihre Bestellungen zu lesen.")
        print("3. Danach landen Sie auf einer anderen Seite. Kopieren Sie deren komplette Adresse aus der")
        print("   Adresszeile des Browsers (sie enthält „code=“) und fügen Sie sie hier ein.")
        print("   Der Code ist nur wenige Minuten gültig.\n")
        try:
            eingabe = input("Adresse: ")
        except (EOFError, KeyboardInterrupt):
            print()
            log.error("Abgebrochen")
            return 1
    try:
        bis = konto.verbinden(eingabe, state)
    except ebay.EbayFehler as fehler:
        log.error("%s", fehler)
        return 1
    log.info("Mit eBay verbunden. Die Freigabe gilt bis %s (gespeichert in %s).",
             formate.datum(bis.astimezone(konfig.speicher.zeitzone).date()), einst.token_datei)
    log.info("Zum Prüfen: tagesrechnung ebay-pruefen")
    return 0


def _ebay_pruefen(args, konfig) -> int:
    einst = _ebay_einstellungen(konfig)
    if einst is None:
        return 2
    zeitzone = konfig.speicher.zeitzone
    heute = datetime.now(zeitzone).date()
    von = heute - timedelta(days=max(args.tage, 0))
    try:
        eingetragen = anschriften.laden(konfig.rechnung.anschriften_datei)
        bestellungen = ebay.abrufen(einst, von, heute, zeitzone, konfig.rechnung.standard_ust_satz,
                                    freigegeben=frozenset(eingetragen))
    except (ebay.EbayFehler, anschriften.AnschriftenFehler) as fehler:
        log.error("%s", fehler)
        return 1
    if not bestellungen:
        print(f"Keine eBay-Bestellungen seit dem {formate.datum(von)}.")
        return 0

    abgerechnet: dict[str, str] = {}
    if konfig.speicher.datenbank.is_file():
        buch = Rechnungsbuch.oeffnen(konfig.speicher.datenbank, nur_kopie=True)
        try:
            abgerechnet = buch.abgerechnet(b.nummer for b in bestellungen)
        finally:
            buch.schliessen()

    symbol = konfig.rechnung.waehrungssymbol
    print(f"{'Bestellung':<16}  {'Datum':<16}  {'Käufer':<24}  {'Betrag':>11}  {'E-Mail':<6}  Status")
    fehler = False
    for b in sorted(bestellungen, key=lambda b: b.zeit):
        if b.nummer in abgerechnet:
            status = f"abgerechnet ({abgerechnet[b.nummer]})"
        elif b.abrechenbar and b.nummer in eingetragen:
            status = f"wird abgerechnet an: {eingetragen[b.nummer].beschreibung()}"
        elif b.abrechenbar:
            status = "wird abgerechnet"
        elif b.fehler:
            status, fehler = f"FEHLER: {b.grund}", True
        else:
            status = b.grund
        betrag = formate.betrag(b.betrag, symbol) if b.betrag is not None else "?"
        print(f"{b.nummer:<16}  {b.zeit:%d.%m.%Y %H:%M}  {b.kaeufer[:24]:<24}  {betrag:>11}  "
              f"{'ja' if b.hat_email else 'NEIN':<6}  {status}")
        if b.hinweis and not b.fehler:
            print(f"{'':<18}Käuferhinweis: „{b.hinweis}“")
    bis = ebay.EbayKonto(einst).freigabe_bis()
    if bis:
        print(f"\neBay-Freigabe gültig bis {formate.datum(bis.astimezone(zeitzone).date())}.")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
