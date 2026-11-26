"""Rechnungsbuch (SQLite): fortlaufende Rechnungsnummern, bereits abgerechnete Bestellungen, Versandstatus.

Das Rechnungsbuch sorgt dafür, dass
* Rechnungsnummern lückenlos und eindeutig vergeben werden,
* keine Bestellung zweimal abgerechnet wird, auch wenn das Programm mehrfach läuft,
* nicht zugestellte Rechnungen beim nächsten Lauf erneut versendet werden – mit derselben Nummer.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .modelle import GespeicherteRechnung, Rechnung

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rechnungen (
    nummer          TEXT PRIMARY KEY,
    rechnungsdatum  TEXT NOT NULL,
    leistungsdatum  TEXT NOT NULL,
    faellig         TEXT NOT NULL,
    kunden_schluessel TEXT NOT NULL,
    kunde_name      TEXT NOT NULL,
    kunde_email     TEXT NOT NULL,
    netto           TEXT NOT NULL,
    steuer          TEXT NOT NULL,
    brutto          TEXT NOT NULL,
    pdf_datei       TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('erstellt', 'versendet')),
    versuche        INTEGER NOT NULL DEFAULT 0,
    letzter_fehler  TEXT NOT NULL DEFAULT '',
    erstellt_am     TEXT NOT NULL,
    versendet_am    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS abgerechnete_bestellungen (
    bestellnummer   TEXT PRIMARY KEY,
    rechnungsnummer TEXT NOT NULL REFERENCES rechnungen(nummer)
);
CREATE TABLE IF NOT EXISTS nummernkreise (
    kreis           INTEGER PRIMARY KEY,  -- Jahr, oder 0 bei jahresübergreifendem Nummernkreis
    letzte_nummer   INTEGER NOT NULL
);
"""


class Rechnungsbuch:
    def __init__(self, verbindung: sqlite3.Connection):
        self._db = verbindung
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)

    @classmethod
    def oeffnen(cls, pfad: Path, nur_kopie: bool = False) -> "Rechnungsbuch":
        """Öffnet das Rechnungsbuch. Mit ``nur_kopie`` wird im Arbeitsspeicher an einer Kopie
        gearbeitet (Probelauf) – die Datei bleibt unverändert."""
        if nur_kopie:
            kopie = sqlite3.connect(":memory:", isolation_level=None)
            if pfad.is_file():
                original = sqlite3.connect(f"{pfad.as_uri()}?mode=ro", uri=True)
                try:
                    original.backup(kopie)
                finally:
                    original.close()
            return cls(kopie)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(pfad, isolation_level=None, timeout=30))

    def schliessen(self) -> None:
        self._db.close()

    @contextmanager
    def transaktion(self) -> Iterator[None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")

    # --- Abfragen ----------------------------------------------------------------------------

    def abgerechnet(self, bestellnummern: Iterable[str]) -> dict[str, str]:
        """Bestellnummer → Rechnungsnummer für alle bereits abgerechneten Bestellungen."""
        nummern = list(bestellnummern)
        ergebnis: dict[str, str] = {}
        for i in range(0, len(nummern), 500):
            teil = nummern[i : i + 500]
            zeilen = self._db.execute(
                f"SELECT bestellnummer, rechnungsnummer FROM abgerechnete_bestellungen "
                f"WHERE bestellnummer IN ({','.join('?' * len(teil))})",
                teil,
            )
            ergebnis.update(dict(zeilen.fetchall()))
        return ergebnis

    def offene(self) -> list[GespeicherteRechnung]:
        """Erstellte, aber noch nicht zugestellte Rechnungen."""
        return self._auflisten("WHERE status = 'erstellt'")

    def alle(self, nur_offene: bool = False) -> list[GespeicherteRechnung]:
        return self.offene() if nur_offene else self._auflisten("")

    def existiert(self, nummer: str) -> bool:
        return self._db.execute("SELECT 1 FROM rechnungen WHERE nummer = ?", (nummer,)).fetchone() is not None

    def holen(self, nummer: str) -> GespeicherteRechnung:
        treffer = self._auflisten("WHERE nummer = ?", (nummer,))
        if not treffer:
            raise KeyError(nummer)
        return treffer[0]

    def _auflisten(self, bedingung: str, parameter: tuple = ()) -> list[GespeicherteRechnung]:
        zeilen = self._db.execute(
            "SELECT nummer, rechnungsdatum, leistungsdatum, faellig, kunde_name, kunde_email, netto, steuer, "
            "brutto, pdf_datei, status, versuche, letzter_fehler, versendet_am, "
            "(SELECT group_concat(bestellnummer, char(31)) FROM "
            "  (SELECT bestellnummer FROM abgerechnete_bestellungen a WHERE a.rechnungsnummer = r.nummer "
            "   ORDER BY a.rowid)) "
            f"FROM rechnungen r {bedingung} ORDER BY erstellt_am, nummer",
            parameter,
        ).fetchall()
        return [
            GespeicherteRechnung(
                nummer=z[0],
                rechnungsdatum=date.fromisoformat(z[1]),
                leistungsdatum=date.fromisoformat(z[2]),
                faellig=date.fromisoformat(z[3]),
                kunde_name=z[4],
                kunde_email=z[5],
                netto=Decimal(z[6]),
                steuer=Decimal(z[7]),
                brutto=Decimal(z[8]),
                pdf_datei=z[9],
                status=z[10],
                versuche=z[11],
                letzter_fehler=z[12],
                versendet_am=z[13],
                bestellnummern=tuple(z[14].split("\x1f")) if z[14] else (),
            )
            for z in zeilen
        ]

    # --- Änderungen (innerhalb von transaktion()) --------------------------------------------

    def naechste_nummer(self, kreis: int, startnummer: int) -> int:
        """Zieht die nächste laufende Nummer im Nummernkreis ``kreis`` (Jahr oder 0)."""
        zeile = self._db.execute("SELECT letzte_nummer FROM nummernkreise WHERE kreis = ?", (kreis,)).fetchone()
        nummer = max(zeile[0] + 1, startnummer) if zeile else startnummer
        self._db.execute(
            "INSERT INTO nummernkreise (kreis, letzte_nummer) VALUES (?, ?) "
            "ON CONFLICT(kreis) DO UPDATE SET letzte_nummer = excluded.letzte_nummer",
            (kreis, nummer),
        )
        return nummer

    def nummernkreise(self) -> dict[int, int]:
        """Nummernkreis (Jahr bzw. 0) → zuletzt vergebene laufende Nummer."""
        return dict(self._db.execute("SELECT kreis, letzte_nummer FROM nummernkreise ORDER BY kreis").fetchall())

    def letzte_nummer_setzen(self, kreis: int, nummer: int) -> None:
        """Schließt an bisherige Rechnungen an (z. B. aus eBay): die nächste Rechnung bekommt ``nummer + 1``.
        Zurücksetzen ist nicht möglich, weil sonst Nummern doppelt vergeben würden."""
        with self.transaktion():
            zeile = self._db.execute("SELECT letzte_nummer FROM nummernkreise WHERE kreis = ?", (kreis,)).fetchone()
            if zeile and nummer < zeile[0]:
                raise ValueError(f"Im Nummernkreis ist bereits die Nummer {zeile[0]} vergeben – zurücksetzen würde "
                                 "doppelte Rechnungsnummern erzeugen")
            self._db.execute(
                "INSERT INTO nummernkreise (kreis, letzte_nummer) VALUES (?, ?) "
                "ON CONFLICT(kreis) DO UPDATE SET letzte_nummer = excluded.letzte_nummer",
                (kreis, nummer),
            )

    def eintragen(self, rechnung: Rechnung, pdf_datei: str, erstellt_am: datetime) -> None:
        s = rechnung.summen
        self._db.execute(
            "INSERT INTO rechnungen (nummer, rechnungsdatum, leistungsdatum, faellig, kunden_schluessel, "
            "kunde_name, kunde_email, netto, steuer, brutto, pdf_datei, status, erstellt_am) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'erstellt', ?)",
            (
                rechnung.nummer,
                rechnung.rechnungsdatum.isoformat(),
                rechnung.leistungsdatum.isoformat(),
                rechnung.faellig.isoformat(),
                rechnung.kunde.schluessel,
                rechnung.kunde.name,
                rechnung.kunde.email,
                str(s.netto),
                str(s.steuer),
                str(s.brutto),
                pdf_datei,
                erstellt_am.isoformat(timespec="seconds"),
            ),
        )
        self._db.executemany(
            "INSERT INTO abgerechnete_bestellungen (bestellnummer, rechnungsnummer) VALUES (?, ?)",
            [(n, rechnung.nummer) for n in rechnung.bestellnummern],
        )

    # --- Versandstatus -----------------------------------------------------------------------

    def als_versendet_markieren(self, nummer: str, zeitpunkt: datetime) -> None:
        self._db.execute(
            "UPDATE rechnungen SET status = 'versendet', versuche = versuche + 1, letzter_fehler = '', "
            "versendet_am = ? WHERE nummer = ?",
            (zeitpunkt.isoformat(timespec="seconds"), nummer),
        )

    def fehlschlag_vermerken(self, nummer: str, fehler: str) -> None:
        self._db.execute(
            "UPDATE rechnungen SET versuche = versuche + 1, letzter_fehler = ? WHERE nummer = ?",
            (fehler[:500], nummer),
        )
