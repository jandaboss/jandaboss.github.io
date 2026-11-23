"""Der Tagesablauf: Bestellungen laden → Rechnungen erstellen → per E-Mail versenden."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from . import formate, pdf, quellen
from .berechnung import bestellungen_bilden, gruppieren, summen_berechnen
from .konfiguration import Konfiguration
from .modelle import Bestellung, GespeicherteRechnung, Rechnung
from .rechnungsbuch import Rechnungsbuch
from .versand import ProbeVersand, SmtpVersand, VersandFehler, nachricht_erstellen

log = logging.getLogger(__name__)


class Versand(Protocol):
    probelauf: bool

    def __enter__(self): ...
    def __exit__(self, *exc): ...
    def senden(self, nachricht, kennung: str) -> None: ...


@dataclass
class Bericht:
    erstellt: list[str] = field(default_factory=list)
    versendet: list[str] = field(default_factory=list)
    fehlgeschlagen: list[tuple[str, str]] = field(default_factory=list)
    datenfehler: list[str] = field(default_factory=list)
    bereits_abgerechnet: int = 0

    @property
    def erfolgreich(self) -> bool:
        return not self.fehlgeschlagen and not self.datenfehler


def ausfuehren(
    konfig: Konfiguration,
    stichtag: date,
    nachholen_tage: int = 0,
    probelauf: bool = False,
    versand: Versand | None = None,
    jetzt: datetime | None = None,
) -> Bericht:
    """Erstellt und versendet die Rechnungen für alle Bestellungen vom ``stichtag``
    (und den ``nachholen_tage`` Tagen davor), die noch nicht abgerechnet sind.

    Zuvor werden Rechnungen erneut zugestellt, deren Versand bei einem früheren Lauf scheiterte.
    Im ``probelauf`` bleibt das Rechnungsbuch unverändert; PDFs und E-Mails (.eml) landen im
    Probelauf-Ordner.
    """
    zeitzone = konfig.speicher.zeitzone
    jetzt = (jetzt or datetime.now(zeitzone)).astimezone(zeitzone)
    von = stichtag - timedelta(days=nachholen_tage)
    pdf_ordner = konfig.speicher.probelauf if probelauf else konfig.speicher.archiv
    if versand is None:
        versand = ProbeVersand(konfig.speicher.probelauf) if probelauf else SmtpVersand(konfig.email)

    bericht = Bericht()
    buch = Rechnungsbuch.oeffnen(konfig.speicher.datenbank, nur_kopie=probelauf)
    try:
        with versand:
            # 1. Rechnungen, deren Versand früher fehlgeschlagen ist, erneut zustellen
            for offen in buch.offene():
                log.info("Erneuter Zustellversuch für %s an %s (bisher %d Versuch(e))",
                         offen.nummer, offen.kunde_email, offen.versuche)
                _zustellen(buch, versand, konfig, offen, konfig.speicher.archiv / offen.pdf_datei, bericht, zeitzone)

            # 2. Neue Bestellungen laden
            zeitraum = formate.datum(stichtag) if von == stichtag else f"{formate.datum(von)}–{formate.datum(stichtag)}"
            try:
                geladen = quellen.laden(konfig.quelle, von, stichtag, zeitzone, konfig.rechnung.standard_ust_satz)
            except quellen.QuellenFehler as fehler:
                log.error("Bestellungen konnten nicht geladen werden: %s", fehler)
                bericht.datenfehler.append(str(fehler))
                return bericht
            for meldung in geladen.fehler:
                log.error("Fehlerhafte Bestelldaten: %s", meldung)
            bericht.datenfehler += geladen.fehler

            bestellungen, fehler = bestellungen_bilden(
                p for p in geladen.positionen if p.bestellnummer not in geladen.fehlerhafte_bestellungen
            )
            for meldung in fehler:
                log.error("%s", meldung)
            bericht.datenfehler += fehler

            schon = buch.abgerechnet(b.nummer for b in bestellungen)
            bericht.bereits_abgerechnet = len(schon)
            neu = [b for b in bestellungen if b.nummer not in schon]
            log.info("Bestellungen %s: %d gefunden, %d bereits abgerechnet, %d neu",
                     zeitraum, len(bestellungen), len(schon), len(neu))

            # 3. Je Gruppe eine Rechnung erstellen und sofort versenden
            for gruppe in gruppieren(neu, konfig.rechnung.eine_rechnung_pro):
                nummern = ", ".join(b.nummer for b in gruppe)
                try:
                    gespeichert, pfad = _rechnung_anlegen(buch, konfig, gruppe, jetzt, pdf_ordner, probelauf)
                except sqlite3.IntegrityError:
                    meldung = f"Bestellung(en) {nummern} wurden gleichzeitig von einem anderen Lauf abgerechnet – übersprungen"
                    log.warning(meldung)
                    continue
                except Exception as fehler:  # noqa: BLE001 – eine kaputte Rechnung darf den Lauf nicht stoppen
                    meldung = f"Rechnung für Bestellung(en) {nummern} konnte nicht erstellt werden: {fehler}"
                    log.exception(meldung)
                    bericht.datenfehler.append(meldung)
                    continue
                bericht.erstellt.append(gespeichert.nummer)
                log.info("Rechnung %s erstellt: %s, %s, Bestellung(en) %s",
                         gespeichert.nummer, gespeichert.kunde_name,
                         formate.betrag(gespeichert.brutto, konfig.rechnung.waehrungssymbol), nummern)
                _zustellen(buch, versand, konfig, gespeichert, pfad, bericht, zeitzone)
    finally:
        buch.schliessen()
    return bericht


def _rechnung_anlegen(
    buch: Rechnungsbuch,
    konfig: Konfiguration,
    gruppe: list[Bestellung],
    jetzt: datetime,
    pdf_ordner: Path,
    probelauf: bool,
) -> tuple[GespeicherteRechnung, Path]:
    einst = konfig.rechnung
    rechnungsdatum = jetzt.date()
    summen = summen_berechnen(
        [p for b in gruppe for p in b.positionen], einst.preise_sind, einst.kleinunternehmer
    )
    jahr = rechnungsdatum.year
    ziel: Path | None = None
    try:
        # Nummer ziehen, PDF schreiben und Rechnung eintragen geschieht in EINER Transaktion:
        # scheitert etwas, wird die Nummer nicht verbraucht und es entsteht keine Lücke.
        with buch.transaktion():
            laufend = buch.naechste_nummer(jahr if einst.nummernkreis_pro_jahr else 0, einst.startnummer)
            nummer = einst.nummer_format.format(jahr=jahr, nummer=laufend)
            if buch.existiert(nummer):
                raise RuntimeError(
                    f"Rechnungsnummer {nummer} ist bereits vergeben – passen nummer_format, startnummer und "
                    "nummernkreis_pro_jahr noch zu den bisherigen Rechnungen?"
                )
            rechnung = Rechnung(
                nummer=nummer,
                rechnungsdatum=rechnungsdatum,
                leistungsdatum=gruppe[0].zeit.date(),
                faellig=rechnungsdatum + timedelta(days=einst.zahlungsziel_tage),
                kunde=gruppe[0].kunde,
                bestellungen=tuple(gruppe),
                summen=summen,
            )
            relativ = f"{jahr}/{formate.dateiname(nummer)}.pdf"
            ziel = pdf_ordner / relativ
            if ziel.exists() and not probelauf:
                # Kann nur ein Überbleibsel eines abgebrochenen Laufs sein: die Nummer steht nicht
                # im Rechnungsbuch, die Datei wurde also nie versendet.
                log.warning("Überschreibe nicht eingetragene PDF-Datei %s", ziel)
            pdf.erzeugen(rechnung, konfig, ziel)
            buch.eintragen(rechnung, relativ, jetzt)
    except BaseException:
        if ziel is not None:
            ziel.unlink(missing_ok=True)
        raise
    return buch.holen(nummer), ziel


def _zustellen(
    buch: Rechnungsbuch,
    versand: Versand,
    konfig: Konfiguration,
    rechnung: GespeicherteRechnung,
    pdf_datei: Path,
    bericht: Bericht,
    zeitzone,
) -> None:
    try:
        nachricht = nachricht_erstellen(konfig, rechnung, pdf_datei)
        versand.senden(nachricht, rechnung.nummer)
    except VersandFehler as fehler:
        buch.fehlschlag_vermerken(rechnung.nummer, str(fehler))
        bericht.fehlgeschlagen.append((rechnung.nummer, str(fehler)))
        log.error("Versand von %s an %s fehlgeschlagen: %s (wird beim nächsten Lauf erneut versucht)",
                  rechnung.nummer, rechnung.kunde_email, fehler)
        return
    buch.als_versendet_markieren(rechnung.nummer, datetime.now(zeitzone))
    bericht.versendet.append(rechnung.nummer)
    if versand.probelauf:
        log.info("Probelauf: E-Mail für %s an %s als .eml gespeichert", rechnung.nummer, rechnung.kunde_email)
    else:
        log.info("Rechnung %s an %s versendet", rechnung.nummer, rechnung.kunde_email)
