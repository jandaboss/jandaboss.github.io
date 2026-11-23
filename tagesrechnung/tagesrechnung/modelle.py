"""Datenklassen für Kunden, Bestellungen und Rechnungen."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .formate import auf_cent


@dataclass(frozen=True)
class Kunde:
    schluessel: str  # Kundennummer oder (falls keine vorhanden) die E-Mail-Adresse
    kunden_nr: str
    name: str
    email: str
    firma: str = ""
    strasse: str = ""
    plz: str = ""
    ort: str = ""
    land: str = ""
    ust_idnr: str = ""

    def anschrift(self) -> list[str]:
        zeilen = [self.firma, self.name, self.strasse, f"{self.plz} {self.ort}".strip(), self.land]
        return [z for z in zeilen if z]


@dataclass(frozen=True)
class Position:
    bestellnummer: str
    bestellzeit: datetime
    kunde: Kunde
    artikel: str
    menge: Decimal
    einzelpreis: Decimal
    ust_satz: Decimal
    einheit: str = ""
    artikelnummer: str = ""

    @property
    def gesamt(self) -> Decimal:
        return auf_cent(self.menge * self.einzelpreis)


@dataclass
class Bestellung:
    nummer: str
    zeit: datetime
    kunde: Kunde
    positionen: list[Position] = field(default_factory=list)


@dataclass(frozen=True)
class Steuergruppe:
    satz: Decimal
    netto: Decimal
    steuer: Decimal
    brutto: Decimal


@dataclass(frozen=True)
class Summen:
    netto: Decimal
    steuer: Decimal
    brutto: Decimal
    gruppen: tuple[Steuergruppe, ...]


@dataclass(frozen=True)
class Rechnung:
    nummer: str
    rechnungsdatum: date
    leistungsdatum: date
    faellig: date
    kunde: Kunde
    bestellungen: tuple[Bestellung, ...]
    summen: Summen

    @property
    def bestellnummern(self) -> list[str]:
        return [b.nummer for b in self.bestellungen]


@dataclass(frozen=True)
class GespeicherteRechnung:
    """Eine Rechnung, wie sie im Rechnungsbuch (SQLite) steht."""

    nummer: str
    rechnungsdatum: date
    leistungsdatum: date
    faellig: date
    kunde_name: str
    kunde_email: str
    netto: Decimal
    steuer: Decimal
    brutto: Decimal
    pdf_datei: str  # relativ zum Archivordner
    status: str  # "erstellt" oder "versendet"
    versuche: int
    letzter_fehler: str
    versendet_am: str
    bestellnummern: tuple[str, ...]
