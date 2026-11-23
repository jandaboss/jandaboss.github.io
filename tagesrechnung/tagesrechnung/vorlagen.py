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
) -> dict[str, str]:
    return {
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
    return _werte(
        konfig, r.nummer, r.rechnungsdatum, r.leistungsdatum, r.faellig, r.kunde.name, r.summen.brutto,
        r.bestellnummern,
    )


def werte_fuer_gespeicherte(g: GespeicherteRechnung, konfig: Konfiguration) -> dict[str, str]:
    return _werte(
        konfig, g.nummer, g.rechnungsdatum, g.leistungsdatum, g.faellig, g.kunde_name, g.brutto, g.bestellnummern
    )
