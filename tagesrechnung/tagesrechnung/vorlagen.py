"""Werte für die Platzhalter in Betreff, E-Mail-Text und Rechnungstexten."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from . import formate
from .konfiguration import Konfiguration
from .modelle import GespeicherteRechnung, Rechnung


def _werte(
    konfig: Konfiguration,
    nummer: str,
    rechnungsdatum: date,
    leistungsdatum: date,
    faellig: date,
    name: str,
    brutto: Decimal,
    bestellnummern: Iterable[str],
    anrede: str,
) -> dict[str, str]:
    return {
        "anrede": anrede,
        "nummer": nummer,
        "datum": formate.datum(rechnungsdatum),
        "rechnungsdatum": formate.datum(rechnungsdatum),
        "leistungsdatum": formate.datum(leistungsdatum),
        "faellig": formate.datum(faellig),
        "name": name,
        "betrag": formate.betrag(brutto, konfig.rechnung.waehrungssymbol),
        "firma": konfig.firma.name,
        "bestellnummern": ", ".join(bestellnummern),
    }


def werte_fuer_rechnung(r: Rechnung, konfig: Konfiguration) -> dict[str, str]:
    # Steht nur eine Firma in der Anschrift, wird die Firma angesprochen
    anrede = f"Guten Tag {r.kunde.name}" if r.kunde.name and r.kunde.name_drucken else "Sehr geehrte Damen und Herren"
    return _werte(
        konfig, r.nummer, r.rechnungsdatum, r.leistungsdatum, r.faellig, r.kunde.name, r.summen.brutto,
        r.bestellnummern, anrede,
    )


def werte_fuer_gespeicherte(g: GespeicherteRechnung, konfig: Konfiguration) -> dict[str, str]:
    anrede = f"Guten Tag {g.kunde_name}" if g.kunde_name else "Guten Tag"
    return _werte(
        konfig, g.nummer, g.rechnungsdatum, g.leistungsdatum, g.faellig, g.kunde_name, g.brutto, g.bestellnummern,
        anrede,
    )
