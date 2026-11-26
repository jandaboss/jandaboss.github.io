"""Abweichende Rechnungsanschriften je Bestellung.

Beispiel: Der Käufer bittet im Bestellhinweis, die Rechnung auf seine Firma auszustellen. Die
Anschrift wird in ``rechnungsanschriften.toml`` eingetragen – am einfachsten mit
``tagesrechnung anschrift BESTELLNUMMER --firma … --strasse … --plz … --ort …`` – und gilt dann
für die Rechnung zu dieser Bestellung. Mit ``--wie-bestellt`` wird eine wegen eines
Käuferhinweises angehaltene Bestellung unverändert freigegeben.

Dateiformat::

    ["12-34567-89012"]
    firma = "Beispiel GmbH"
    strasse = "Musterstraße 1"
    plz = "70191"
    ort = "Stuttgart"
    # name = "…"       ohne Angabe bleibt der Name des Käufers stehen
    # ust_idnr = "…"

    ["12-34567-89012"]
    wie_bestellt = true
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from .modelle import Kunde

_TEXTFELDER = ("firma", "name", "strasse", "plz", "ort", "land", "ust_idnr")


class AnschriftenFehler(Exception):
    pass


@dataclass(frozen=True)
class Anschrift:
    wie_bestellt: bool = False
    firma: str = ""
    name: str | None = None  # None: Name des Käufers beibehalten
    strasse: str = ""
    plz: str = ""
    ort: str = ""
    land: str = ""
    ust_idnr: str = ""

    def anwenden(self, kunde: Kunde, bestellnummer: str) -> Kunde:
        if self.wie_bestellt:
            return kunde
        return replace(
            kunde,
            # eigene Rechnung, nicht mit anderen Bestellungen des Käufers zusammenfassen
            schluessel=f"{kunde.schluessel}#{bestellnummer}",
            firma=self.firma,
            name=kunde.name if self.name is None else self.name,
            strasse=self.strasse,
            plz=self.plz,
            ort=self.ort,
            land=self.land,
            ust_idnr=self.ust_idnr,
        )

    def beschreibung(self) -> str:
        if self.wie_bestellt:
            return "wie bestellt freigegeben"
        return ", ".join(t for t in (self.firma, self.name, self.strasse, f"{self.plz} {self.ort}".strip()) if t)


def laden(pfad: Path | None) -> dict[str, Anschrift]:
    if pfad is None or not pfad.is_file():
        return {}
    try:
        with pfad.open("rb") as f:
            daten = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise AnschriftenFehler(f"{pfad}: ungültiges Format – {e}") from None

    anschriften: dict[str, Anschrift] = {}
    for nummer, eintrag in daten.items():
        wo = f"{pfad.name} [{nummer!r}]"
        if not isinstance(eintrag, dict):
            raise AnschriftenFehler(f"{wo}: muss ein Abschnitt sein, z. B. [\"{nummer}\"]")
        unbekannt = set(eintrag) - set(_TEXTFELDER) - {"wie_bestellt"}
        if unbekannt:
            raise AnschriftenFehler(f"{wo}: unbekannte Angabe {sorted(unbekannt)[0]!r}")
        if not all(isinstance(eintrag.get(f, ""), str) for f in _TEXTFELDER):
            raise AnschriftenFehler(f"{wo}: alle Angaben müssen Text in Anführungszeichen sein")
        if eintrag.get("wie_bestellt") is True:
            anschriften[str(nummer)] = Anschrift(wie_bestellt=True)
            continue
        werte = {f: eintrag[f].strip() for f in _TEXTFELDER if f in eintrag}
        anschrift = Anschrift(**werte)
        _pruefen(anschrift, wo)
        anschriften[str(nummer)] = anschrift
    return anschriften


def _pruefen(a: Anschrift, wo: str) -> None:
    if not (a.firma or a.name):
        raise AnschriftenFehler(f"{wo}: firma oder name angeben")
    fehlend = [f for f in ("strasse", "plz", "ort") if not getattr(a, f)]
    if fehlend:
        raise AnschriftenFehler(f"{wo}: es fehlt {', '.join(fehlend)}")


def eintragen(pfad: Path, bestellnummer: str, anschrift: Anschrift) -> None:
    """Hängt einen Eintrag an die Datei an (legt sie bei Bedarf an)."""
    bestellnummer = bestellnummer.strip()
    if not bestellnummer:
        raise AnschriftenFehler("Bestellnummer fehlt")
    if not anschrift.wie_bestellt:
        _pruefen(anschrift, bestellnummer)
    if bestellnummer in laden(pfad):
        raise AnschriftenFehler(
            f"Für Bestellung {bestellnummer} ist schon etwas eingetragen. Bitte direkt in {pfad} ändern."
        )

    def text(wert: str) -> str:  # TOML-Zeichenkette (JSON-Escapes sind gültiges TOML)
        return json.dumps(wert, ensure_ascii=False)

    zeilen = [f"[{text(bestellnummer)}]"]
    if anschrift.wie_bestellt:
        zeilen.append("wie_bestellt = true")
    else:
        for feld in _TEXTFELDER:
            wert = getattr(anschrift, feld)
            if wert is not None and (wert or feld == "name"):
                zeilen.append(f"{feld} = {text(wert)}")
    pfad.parent.mkdir(parents=True, exist_ok=True)
    neu = not pfad.exists() or pfad.stat().st_size == 0
    with pfad.open("a", encoding="utf-8") as f:
        if neu:
            f.write("# Abweichende Rechnungsanschriften je Bestellung (siehe README)\n")
        f.write("\n" + "\n".join(zeilen) + "\n")
