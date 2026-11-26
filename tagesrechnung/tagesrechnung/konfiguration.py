"""Lädt und prüft die Konfigurationsdatei (TOML)."""

from __future__ import annotations

import os
import string
import tomllib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .formate import datum_lesen, zahl_lesen


class KonfigurationsFehler(Exception):
    pass


# Interne Feldnamen der Bestelldaten → Pflichtfeld?
FELDER: dict[str, bool] = {
    "bestellnummer": True,
    "bestelldatum": True,
    "kunden_nr": False,
    "kunde_name": True,
    "kunde_firma": False,
    "kunde_strasse": False,
    "kunde_plz": False,
    "kunde_ort": False,
    "kunde_land": False,
    "kunde_email": True,
    "kunde_ust_idnr": False,
    "artikel": True,
    "artikelnummer": False,
    "menge": True,
    "einheit": False,
    "einzelpreis": True,
    "ust_satz": False,
}

# Platzhalter, die in Betreff, E-Mail-Text und Rechnungstexten erlaubt sind
PLATZHALTER = {
    "anrede": "Guten Tag Erika Musterfrau",  # bei Rechnungen nur an eine Firma: „Sehr geehrte Damen und Herren“
    "nummer": "RE-2026-00001",
    "datum": "22.11.2026",
    "rechnungsdatum": "22.11.2026",
    "leistungsdatum": "22.11.2026",
    "faellig": "06.12.2026",
    "name": "Erika Musterfrau",
    "betrag": "119,00 €",
    "firma": "Muster GmbH",
    "bestellnummern": "1001, 1002",
}

STANDARD_EINLEITUNG = (
    "{anrede},\n\n"
    "vielen Dank für Ihre Bestellung. Hiermit stellen wir Ihnen folgende Lieferungen und Leistungen in Rechnung:"
)
STANDARD_ZAHLUNGSHINWEIS = (
    "Bitte überweisen Sie den Rechnungsbetrag von {betrag} bis zum {faellig} "
    "unter Angabe der Rechnungsnummer {nummer} auf das unten genannte Konto."
)
STANDARD_SCHLUSSTEXT = "Vielen Dank für Ihren Einkauf!"
STANDARD_BETREFF = "Ihre Rechnung {nummer} vom {datum}"
STANDARD_MAILTEXT = (
    "Guten Tag {name},\n\n"
    "vielen Dank für Ihre Bestellung ({bestellnummern}).\n"
    "Im Anhang erhalten Sie Ihre Rechnung {nummer} über {betrag} als PDF.\n\n"
    "Mit freundlichen Grüßen\n"
    "{firma}\n"
)


@dataclass
class Firma:
    name: str
    strasse: str
    plz: str
    ort: str
    inhaber: str = ""  # bei Einzelunternehmen mit Geschäftsbezeichnung: vollständiger Name des Inhabers
    land: str = ""
    telefon: str = ""
    email: str = ""
    webseite: str = ""
    steuernummer: str = ""
    ust_idnr: str = ""
    geschaeftsfuehrung: str = ""
    handelsregister: str = ""
    bank: str = ""
    iban: str = ""
    bic: str = ""
    logo: Path | None = None

    def absenderzeile(self) -> str:
        teile = (self.name, self.inhaber, self.strasse, f"{self.plz} {self.ort}".strip())
        return " · ".join(t for t in teile if t)


@dataclass
class RechnungsEinstellungen:
    nummer_format: str = "RE-{jahr}-{nummer:05d}"
    nummernkreis_pro_jahr: bool = True
    startnummer: int = 1
    eine_rechnung_pro: str = "kunde"  # "kunde" (Sammelrechnung je Kunde und Tag) oder "bestellung"
    preise_sind: str = "brutto"  # "brutto" oder "netto"
    standard_ust_satz: Decimal = Decimal("19")
    kleinunternehmer: bool = False
    zahlungsziel_tage: int = 14
    abrechnen_ab: date | None = None  # ältere Bestellungen werden nie abgerechnet
    anschriften_datei: Path | None = None  # abweichende Rechnungsanschriften je Bestellung
    einleitung: str = STANDARD_EINLEITUNG
    zahlungshinweis: str = STANDARD_ZAHLUNGSHINWEIS
    schlusstext: str = STANDARD_SCHLUSSTEXT
    waehrungssymbol: str = "€"
    schrift: Path | None = None
    schrift_fett: Path | None = None


@dataclass
class EbayEinstellungen:
    client_id: str
    ru_name: str
    token_datei: Path
    client_secret: str = ""
    client_secret_env: str = "TAGESRECHNUNG_EBAY_SECRET"
    umgebung: str = "produktion"  # "produktion" oder "sandbox"
    nur_bezahlte: bool = True
    versandkosten_text: str = "Versandkosten"
    ust_saetze: dict[str, Decimal] = field(default_factory=dict)  # SKU → abweichender Steuersatz

    def secret(self) -> str:
        return os.environ.get(self.client_secret_env) or self.client_secret


@dataclass
class QuellenEinstellungen:
    typ: str  # "csv", "sqlite" oder "ebay"
    pfad: Path | None
    trennzeichen: str = "auto"
    kodierung: str = "utf-8-sig"
    abfrage: str = ""
    spalten: dict[str, str] = field(default_factory=dict)  # interner Name → Spaltenname in der Quelle
    ebay: EbayEinstellungen | None = None

    def spaltenname(self, feld: str) -> str:
        return self.spalten.get(feld, feld)


@dataclass
class EmailEinstellungen:
    absender: str
    absender_name: str = ""
    smtp_server: str = ""
    smtp_port: int = 587
    sicherheit: str = "starttls"  # "starttls", "ssl" oder "keine"
    benutzer: str = ""
    passwort: str = ""
    passwort_env: str = "TAGESRECHNUNG_SMTP_PASSWORT"
    antwort_an: str = ""
    bcc: list[str] = field(default_factory=list)
    betreff: str = STANDARD_BETREFF
    text: str = STANDARD_MAILTEXT
    zeitlimit_sekunden: int = 30


@dataclass
class SpeicherEinstellungen:
    archiv: Path
    datenbank: Path
    probelauf: Path
    zeitzone: ZoneInfo


@dataclass
class Konfiguration:
    firma: Firma
    rechnung: RechnungsEinstellungen
    quelle: QuellenEinstellungen
    email: EmailEinstellungen
    speicher: SpeicherEinstellungen
    datei: Path


def laden(pfad: str | Path) -> Konfiguration:
    pfad = Path(pfad).expanduser().resolve()
    if not pfad.is_file():
        raise KonfigurationsFehler(f"Konfigurationsdatei nicht gefunden: {pfad}")
    try:
        with pfad.open("rb") as f:
            daten = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise KonfigurationsFehler(f"{pfad}: ungültiges TOML – {e}") from None

    basis = pfad.parent
    quelle = _quelle(_abschnitt(daten, "quelle"), basis)
    if quelle.typ == "ebay":
        quelle.ebay = _ebay(_abschnitt(daten, "ebay"), basis)
    return Konfiguration(
        firma=_firma(_abschnitt(daten, "firma"), basis),
        rechnung=_rechnung(_abschnitt(daten, "rechnung", pflicht=False), basis),
        quelle=quelle,
        email=_email(_abschnitt(daten, "email")),
        speicher=_speicher(_abschnitt(daten, "speicher", pflicht=False), basis),
        datei=pfad,
    )


# --- Hilfsfunktionen -------------------------------------------------------------------------


def _abschnitt(daten: dict, name: str, pflicht: bool = True) -> "_Leser":
    wert = daten.get(name)
    if wert is None:
        if pflicht:
            raise KonfigurationsFehler(f"Abschnitt [{name}] fehlt in der Konfiguration")
        wert = {}
    if not isinstance(wert, dict):
        raise KonfigurationsFehler(f"[{name}] muss ein Abschnitt sein")
    return _Leser(name, wert)


class _Leser:
    def __init__(self, name: str, daten: dict):
        self.name = name
        self.daten = daten

    def _fehler(self, schluessel: str, text: str) -> KonfigurationsFehler:
        return KonfigurationsFehler(f"[{self.name}] {schluessel}: {text}")

    def text(self, schluessel: str, standard: str | None = None) -> str:
        wert = self.daten.get(schluessel, standard)
        if wert is None:
            raise self._fehler(schluessel, "Angabe fehlt")
        if not isinstance(wert, str):
            raise self._fehler(schluessel, "muss ein Text in Anführungszeichen sein")
        return wert.strip() if "\n" not in wert else wert

    def ganzzahl(self, schluessel: str, standard: int) -> int:
        wert = self.daten.get(schluessel, standard)
        if not isinstance(wert, int) or isinstance(wert, bool):
            raise self._fehler(schluessel, "muss eine ganze Zahl sein")
        return wert

    def schalter(self, schluessel: str, standard: bool) -> bool:
        wert = self.daten.get(schluessel, standard)
        if not isinstance(wert, bool):
            raise self._fehler(schluessel, "muss true oder false sein")
        return wert

    def auswahl(self, schluessel: str, standard: str, erlaubt: tuple[str, ...]) -> str:
        wert = self.text(schluessel, standard).lower()
        if wert not in erlaubt:
            raise self._fehler(schluessel, f"erlaubt sind: {', '.join(erlaubt)}")
        return wert

    def pfad(self, schluessel: str, basis: Path, standard: str | None = None) -> Path | None:
        wert = self.text(schluessel, standard or "")
        if not wert:
            return None
        p = Path(wert).expanduser()
        return p if p.is_absolute() else (basis / p).resolve()

    def vorlage(self, schluessel: str, standard: str) -> str:
        wert = self.text(schluessel, standard)
        _vorlage_pruefen(wert, lambda t: self._fehler(schluessel, t))
        return wert


def _vorlage_pruefen(vorlage: str, fehler) -> None:
    try:
        felder = {f for _, f, _, _ in string.Formatter().parse(vorlage) if f is not None}
        unbekannt = felder - PLATZHALTER.keys()
        if unbekannt:
            raise fehler(
                f"unbekannter Platzhalter {{{sorted(unbekannt)[0]}}} – erlaubt: "
                + ", ".join("{" + p + "}" for p in PLATZHALTER)
            )
        vorlage.format_map(PLATZHALTER)
    except (ValueError, IndexError) as e:
        raise fehler(f"fehlerhafte Vorlage ({e}); geschweifte Klammern als {{{{ und }}}} schreiben") from None


def _firma(l: _Leser, basis: Path) -> Firma:
    firma = Firma(
        name=l.text("name"),
        strasse=l.text("strasse"),
        plz=l.text("plz"),
        ort=l.text("ort"),
        inhaber=l.text("inhaber", ""),
        land=l.text("land", ""),
        telefon=l.text("telefon", ""),
        email=l.text("email", ""),
        webseite=l.text("webseite", ""),
        steuernummer=l.text("steuernummer", ""),
        ust_idnr=l.text("ust_idnr", ""),
        geschaeftsfuehrung=l.text("geschaeftsfuehrung", ""),
        handelsregister=l.text("handelsregister", ""),
        bank=l.text("bank", ""),
        iban=l.text("iban", ""),
        bic=l.text("bic", ""),
        logo=l.pfad("logo", basis),
    )
    if not (firma.steuernummer or firma.ust_idnr):
        raise KonfigurationsFehler(
            "[firma] steuernummer oder ust_idnr muss angegeben sein (Pflichtangabe nach § 14 Abs. 4 UStG)"
        )
    if firma.logo and not firma.logo.is_file():
        raise KonfigurationsFehler(f"[firma] logo: Datei nicht gefunden: {firma.logo}")
    return firma


def _rechnung(l: _Leser, basis: Path) -> RechnungsEinstellungen:
    nummer_format = l.text("nummer_format", RechnungsEinstellungen.nummer_format)
    pro_jahr = l.schalter("nummernkreis_pro_jahr", True)
    try:
        felder = {f.split(":")[0] for _, f, _, _ in string.Formatter().parse(nummer_format) if f}
        nummer_format.format(jahr=2026, nummer=1)
    except (ValueError, KeyError, IndexError) as e:
        raise KonfigurationsFehler(f"[rechnung] nummer_format ist fehlerhaft: {e}") from None
    if "nummer" not in felder:
        raise KonfigurationsFehler("[rechnung] nummer_format muss den Platzhalter {nummer} enthalten")
    if pro_jahr and "jahr" not in felder:
        raise KonfigurationsFehler(
            "[rechnung] Bei nummernkreis_pro_jahr = true muss nummer_format den Platzhalter {jahr} enthalten, "
            "sonst wären die Rechnungsnummern nicht eindeutig."
        )

    try:
        ust = zahl_lesen(l.daten.get("standard_ust_satz", "19"))
    except ValueError:
        raise KonfigurationsFehler("[rechnung] standard_ust_satz muss eine Zahl sein") from None

    startnummer = l.ganzzahl("startnummer", 1)
    zahlungsziel = l.ganzzahl("zahlungsziel_tage", 14)
    if startnummer < 1 or zahlungsziel < 0:
        raise KonfigurationsFehler("[rechnung] startnummer muss ≥ 1 und zahlungsziel_tage ≥ 0 sein")

    abrechnen_ab = l.daten.get("abrechnen_ab")
    if isinstance(abrechnen_ab, str):
        try:
            abrechnen_ab = datum_lesen(abrechnen_ab, date.today())
        except ValueError as e:
            raise KonfigurationsFehler(f"[rechnung] abrechnen_ab: {e}") from None
    if abrechnen_ab is not None and not isinstance(abrechnen_ab, date):
        raise KonfigurationsFehler("[rechnung] abrechnen_ab muss ein Datum sein, z. B. 2026-11-24")

    return RechnungsEinstellungen(
        nummer_format=nummer_format,
        nummernkreis_pro_jahr=pro_jahr,
        startnummer=startnummer,
        eine_rechnung_pro=l.auswahl("eine_rechnung_pro", "kunde", ("kunde", "bestellung")),
        preise_sind=l.auswahl("preise_sind", "brutto", ("brutto", "netto")),
        standard_ust_satz=ust,
        kleinunternehmer=l.schalter("kleinunternehmer", False),
        zahlungsziel_tage=zahlungsziel,
        abrechnen_ab=abrechnen_ab,
        anschriften_datei=l.pfad("anschriften", basis, "rechnungsanschriften.toml"),
        einleitung=l.vorlage("einleitung", STANDARD_EINLEITUNG),
        zahlungshinweis=l.vorlage("zahlungshinweis", STANDARD_ZAHLUNGSHINWEIS),
        schlusstext=l.vorlage("schlusstext", STANDARD_SCHLUSSTEXT),
        waehrungssymbol=l.text("waehrungssymbol", "€"),
        schrift=_datei_pruefen(l, "schrift", basis),
        schrift_fett=_datei_pruefen(l, "schrift_fett", basis),
    )


def _datei_pruefen(l: _Leser, schluessel: str, basis: Path) -> Path | None:
    p = l.pfad(schluessel, basis)
    if p and not p.is_file():
        raise KonfigurationsFehler(f"[{l.name}] {schluessel}: Datei nicht gefunden: {p}")
    return p


def _quelle(l: _Leser, basis: Path) -> QuellenEinstellungen:
    typ = l.auswahl("typ", "csv", ("csv", "sqlite", "ebay"))
    pfad = l.pfad("pfad", basis)
    if pfad is None and typ != "ebay":
        raise KonfigurationsFehler("[quelle] pfad: Angabe fehlt")

    spalten_roh = l.daten.get("spalten", {})
    if not isinstance(spalten_roh, dict):
        raise KonfigurationsFehler("[quelle.spalten] muss ein Abschnitt sein")
    spalten: dict[str, str] = {}
    for feld, spalte in spalten_roh.items():
        if feld not in FELDER:
            raise KonfigurationsFehler(
                f"[quelle.spalten] unbekanntes Feld {feld!r} – erlaubt: {', '.join(FELDER)}"
            )
        if not isinstance(spalte, str) or not spalte.strip():
            raise KonfigurationsFehler(f"[quelle.spalten] {feld}: Spaltenname muss ein Text sein")
        spalten[feld] = spalte.strip()

    abfrage = l.text("abfrage", "")
    if typ == "sqlite" and not abfrage:
        raise KonfigurationsFehler("[quelle] abfrage: Für typ = \"sqlite\" wird eine SQL-Abfrage benötigt")

    trennzeichen = l.text("trennzeichen", "auto")
    if trennzeichen == "\\t":
        trennzeichen = "\t"
    if trennzeichen != "auto" and len(trennzeichen) != 1:
        raise KonfigurationsFehler("[quelle] trennzeichen muss genau ein Zeichen oder \"auto\" sein")

    return QuellenEinstellungen(
        typ=typ,
        pfad=pfad,
        trennzeichen=trennzeichen,
        kodierung=l.text("kodierung", "utf-8-sig"),
        abfrage=abfrage,
        spalten=spalten,
    )


def _ebay(l: _Leser, basis: Path) -> EbayEinstellungen:
    saetze_roh = l.daten.get("ust_saetze", {})
    if not isinstance(saetze_roh, dict):
        raise KonfigurationsFehler("[ebay.ust_saetze] muss ein Abschnitt sein: \"SKU\" = Steuersatz")
    saetze: dict[str, Decimal] = {}
    for sku, satz in saetze_roh.items():
        try:
            saetze[str(sku).strip()] = zahl_lesen(satz)
        except ValueError:
            raise KonfigurationsFehler(f"[ebay.ust_saetze] {sku}: Steuersatz muss eine Zahl sein") from None
    return EbayEinstellungen(
        client_id=l.text("client_id"),
        ru_name=l.text("ru_name"),
        token_datei=l.pfad("token_datei", basis, "ebay-zugang.json"),
        client_secret=l.text("client_secret", ""),
        client_secret_env=l.text("client_secret_env", "TAGESRECHNUNG_EBAY_SECRET"),
        umgebung=l.auswahl("umgebung", "produktion", ("produktion", "sandbox")),
        nur_bezahlte=l.schalter("nur_bezahlte", True),
        versandkosten_text=l.text("versandkosten_text", "Versandkosten"),
        ust_saetze=saetze,
    )


def _email(l: _Leser) -> EmailEinstellungen:
    absender = l.text("absender")
    if "@" not in absender:
        raise KonfigurationsFehler("[email] absender muss eine E-Mail-Adresse sein")
    bcc = l.daten.get("bcc", [])
    if isinstance(bcc, str):
        bcc = [bcc] if bcc.strip() else []
    if not isinstance(bcc, list) or not all(isinstance(a, str) and "@" in a for a in bcc):
        raise KonfigurationsFehler("[email] bcc muss eine E-Mail-Adresse oder eine Liste davon sein")
    return EmailEinstellungen(
        absender=absender,
        absender_name=l.text("absender_name", ""),
        smtp_server=l.text("smtp_server", ""),
        smtp_port=l.ganzzahl("smtp_port", 587),
        sicherheit=l.auswahl("sicherheit", "starttls", ("starttls", "ssl", "keine")),
        benutzer=l.text("benutzer", ""),
        passwort=l.text("passwort", ""),
        passwort_env=l.text("passwort_env", "TAGESRECHNUNG_SMTP_PASSWORT"),
        antwort_an=l.text("antwort_an", ""),
        bcc=[a.strip() for a in bcc],
        betreff=l.vorlage("betreff", STANDARD_BETREFF),
        text=l.vorlage("text", STANDARD_MAILTEXT),
        zeitlimit_sekunden=l.ganzzahl("zeitlimit_sekunden", 30),
    )


def _speicher(l: _Leser, basis: Path) -> SpeicherEinstellungen:
    name = l.text("zeitzone", "Europe/Berlin")
    try:
        zeitzone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise KonfigurationsFehler(
            f"[speicher] zeitzone: unbekannte Zeitzone {name!r} (unter Windows: pip install tzdata)"
        ) from None
    return SpeicherEinstellungen(
        archiv=l.pfad("archiv", basis, "archiv"),
        datenbank=l.pfad("datenbank", basis, "rechnungsbuch.sqlite"),
        probelauf=l.pfad("probelauf", basis, "probelauf"),
        zeitzone=zeitzone,
    )
