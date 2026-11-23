"""Liest Bestellpositionen aus einer CSV-Datei oder einer SQLite-Datenbank.

Jede Zeile der Quelle ist eine Bestellposition. Zusammengehörige Positionen haben dieselbe
Bestellnummer. Welche Spalten erwartet werden, steht in ``konfiguration.FELDER``; abweichende
Spaltennamen lassen sich unter ``[quelle.spalten]`` zuordnen.
"""

from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .formate import zahl_lesen, zeitpunkt_lesen
from .konfiguration import FELDER, QuellenEinstellungen
from .modelle import Kunde, Position


class QuellenFehler(Exception):
    """Die Quelle kann gar nicht gelesen werden (Datei fehlt, Spalten fehlen, SQL-Fehler …)."""


@dataclass
class Ladeergebnis:
    positionen: list[Position] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)
    # Bestellungen mit mindestens einer fehlerhaften Zeile – sie werden komplett übersprungen,
    # damit keine unvollständige Rechnung entsteht.
    fehlerhafte_bestellungen: set[str] = field(default_factory=set)


def laden(
    quelle: QuellenEinstellungen,
    von: date,
    bis: date,
    zeitzone: ZoneInfo,
    standard_ust_satz: Decimal,
) -> Ladeergebnis:
    """Alle Positionen, deren Bestelldatum (in ``zeitzone``) zwischen ``von`` und ``bis`` liegt."""
    if quelle.typ == "csv":
        zeilen = _csv_zeilen(quelle)
    else:
        zeilen = _sqlite_zeilen(quelle, von, bis)

    ergebnis = Ladeergebnis()
    for ort, zeile in zeilen:
        if not any(str(w).strip() for w in zeile.values() if w is not None):
            continue  # Leerzeile
        bestellnummer = str(zeile.get("bestellnummer") or "").strip()
        try:
            zeit = zeitpunkt_lesen(zeile.get("bestelldatum"), zeitzone)
        except ValueError as e:
            ergebnis.fehler.append(f"{ort}: Bestelldatum – {e}")
            if bestellnummer:
                ergebnis.fehlerhafte_bestellungen.add(bestellnummer)
            continue
        if not (von <= zeit.date() <= bis):
            continue
        try:
            ergebnis.positionen.append(_position(zeile, zeit, standard_ust_satz))
        except ValueError as e:
            ergebnis.fehler.append(f"{ort} (Bestellung {bestellnummer or '?'}): {e}")
            if bestellnummer:
                ergebnis.fehlerhafte_bestellungen.add(bestellnummer)
    return ergebnis


def _position(zeile: dict, zeit, standard_ust_satz: Decimal) -> Position:
    def text(feld: str) -> str:
        wert = zeile.get(feld)
        wert = "" if wert is None else str(wert).strip()
        if not wert and FELDER[feld]:
            raise ValueError(f"Pflichtfeld {feld!r} ist leer")
        return wert

    def zahl(feld: str, standard: Decimal | None = None) -> Decimal:
        roh = zeile.get(feld)
        if (roh is None or str(roh).strip() == "") and standard is not None:
            return standard
        try:
            return zahl_lesen(roh)
        except ValueError as e:
            raise ValueError(f"{feld}: {e}") from None

    email = text("kunde_email")
    if "@" not in email or " " in email:
        raise ValueError(f"ungültige E-Mail-Adresse {email!r}")
    kunden_nr = text("kunden_nr")
    kunde = Kunde(
        schluessel=kunden_nr or email.lower(),
        kunden_nr=kunden_nr,
        name=text("kunde_name"),
        email=email,
        firma=text("kunde_firma"),
        strasse=text("kunde_strasse"),
        plz=text("kunde_plz"),
        ort=text("kunde_ort"),
        land=text("kunde_land"),
        ust_idnr=text("kunde_ust_idnr"),
    )
    menge = zahl("menge")
    if menge == 0:
        raise ValueError("menge ist 0")
    ust_satz = zahl("ust_satz", standard_ust_satz)
    if ust_satz < 0 or ust_satz >= 100:
        raise ValueError(f"unplausibler Umsatzsteuersatz {ust_satz}")
    return Position(
        bestellnummer=text("bestellnummer"),
        bestellzeit=zeit,
        kunde=kunde,
        artikel=text("artikel"),
        artikelnummer=text("artikelnummer"),
        menge=menge,
        einheit=text("einheit"),
        einzelpreis=zahl("einzelpreis"),
        ust_satz=ust_satz,
    )


def _zuordnung(quelle: QuellenEinstellungen, spalten: list[str], wo: str) -> dict[str, str]:
    """Ordnet jedem internen Feld die tatsächliche Spalte zu (Groß-/Kleinschreibung egal)."""
    vorhanden = {s.strip().lstrip("﻿").lower(): s for s in spalten if s is not None}
    zuordnung: dict[str, str] = {}
    fehlend: list[str] = []
    for feld, pflicht in FELDER.items():
        gesucht = quelle.spaltenname(feld)
        spalte = vorhanden.get(gesucht.lower())
        if spalte is not None:
            zuordnung[feld] = spalte
        elif pflicht:
            fehlend.append(gesucht if gesucht == feld else f"{gesucht} (für {feld})")
    if fehlend:
        raise QuellenFehler(
            f"{wo}: Pflichtspalten fehlen: {', '.join(fehlend)}. "
            f"Vorhandene Spalten: {', '.join(s for s in spalten if s)}. "
            "Abweichende Spaltennamen können unter [quelle.spalten] zugeordnet werden."
        )
    return zuordnung


def _trennzeichen_erkennen(kopfzeile: str) -> str:
    kandidaten = [";", ",", "\t", "|"]
    return max(kandidaten, key=kopfzeile.count)


def _csv_zeilen(quelle: QuellenEinstellungen) -> Iterator[tuple[str, dict]]:
    try:
        f = quelle.pfad.open(newline="", encoding=quelle.kodierung)
    except FileNotFoundError:
        raise QuellenFehler(f"Bestelldatei nicht gefunden: {quelle.pfad}") from None
    except LookupError:
        raise QuellenFehler(f"[quelle] kodierung: unbekannte Zeichenkodierung {quelle.kodierung!r}") from None

    def zeilen() -> Iterator[tuple[str, dict]]:
        with f:
            try:
                kopf = f.readline()
                if not kopf.strip():
                    return
                trenn = quelle.trennzeichen
                if trenn == "auto":
                    trenn = _trennzeichen_erkennen(kopf)
                f.seek(0)
                leser = csv.DictReader(f, delimiter=trenn)
                zuordnung = _zuordnung(quelle, list(leser.fieldnames or []), str(quelle.pfad))
                for roh in leser:
                    ort = f"{quelle.pfad.name}, Zeile {leser.line_num}"
                    yield ort, {feld: roh.get(spalte) for feld, spalte in zuordnung.items()}
            except UnicodeDecodeError:
                raise QuellenFehler(
                    f"{quelle.pfad}: Zeichenkodierung passt nicht. Für Excel-Exporte unter Windows "
                    "hilft oft kodierung = \"cp1252\"."
                ) from None
            except csv.Error as e:
                raise QuellenFehler(f"{quelle.pfad}: CSV-Fehler – {e}") from None

    return zeilen()


def _sqlite_zeilen(quelle: QuellenEinstellungen, von: date, bis: date) -> Iterator[tuple[str, dict]]:
    if not quelle.pfad.is_file():
        raise QuellenFehler(f"Datenbank nicht gefunden: {quelle.pfad}")
    parameter = {
        "von": von.isoformat(),
        "bis": bis.isoformat(),
        "bis_exklusiv": (bis + timedelta(days=1)).isoformat(),
    }
    try:
        verbindung = sqlite3.connect(f"{quelle.pfad.as_uri()}?mode=ro", uri=True)
        try:
            cursor = verbindung.execute(quelle.abfrage, parameter)
            spalten = [d[0] for d in cursor.description or []]
            ergebnisse = cursor.fetchall()
        finally:
            verbindung.close()
    except sqlite3.Error as e:
        raise QuellenFehler(f"SQL-Abfrage fehlgeschlagen: {e}") from None

    zuordnung = _zuordnung(quelle, spalten, "SQL-Abfrage")
    index = {feld: spalten.index(spalte) for feld, spalte in zuordnung.items()}

    def zeilen() -> Iterator[tuple[str, dict]]:
        for nr, werte in enumerate(ergebnisse, start=1):
            yield f"Datenbankzeile {nr}", {feld: werte[i] for feld, i in index.items()}

    return zeilen()
