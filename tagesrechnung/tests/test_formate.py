from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tagesrechnung import formate

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.mark.parametrize(
    "eingabe, erwartet",
    [
        ("12,50", "12.50"),
        ("12.50", "12.50"),
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("9,99 €", "9.99"),
        ("19 %", "19"),
        ("-5,00", "-5.00"),
        (" 3 ", "3"),
        (7, "7"),
        (24.9, "24.9"),
    ],
)
def test_zahl_lesen(eingabe, erwartet):
    assert formate.zahl_lesen(eingabe) == Decimal(erwartet)


@pytest.mark.parametrize("eingabe", ["", None, "abc", "1,2,3", "NaN", True])
def test_zahl_lesen_fehler(eingabe):
    with pytest.raises(ValueError):
        formate.zahl_lesen(eingabe)


@pytest.mark.parametrize(
    "eingabe, erwartet",
    [
        ("2026-11-22", datetime(2026, 11, 22)),
        ("2026-11-22 14:35", datetime(2026, 11, 22, 14, 35)),
        ("2026-11-22T14:35:10", datetime(2026, 11, 22, 14, 35, 10)),
        ("22.11.2026", datetime(2026, 11, 22)),
        ("22.11.2026 14:35", datetime(2026, 11, 22, 14, 35)),
        # UTC-Zeitpunkt kurz vor Mitternacht gehört in Berlin schon zum nächsten Tag
        ("2026-11-22T23:30:00Z", datetime(2026, 11, 23, 0, 30)),
        ("2026-07-01T12:00:00+00:00", datetime(2026, 7, 1, 14, 0)),  # Sommerzeit
    ],
)
def test_zeitpunkt_lesen(eingabe, erwartet):
    assert formate.zeitpunkt_lesen(eingabe, BERLIN) == erwartet


def test_zeitpunkt_lesen_unixzeit():
    assert formate.zeitpunkt_lesen(1795388400, BERLIN) == datetime(2026, 11, 23, 0, 0)  # 22.11. 23:00 UTC


def test_zeitpunkt_lesen_fehler():
    with pytest.raises(ValueError):
        formate.zeitpunkt_lesen("gestern Abend", BERLIN)


def test_datum_lesen():
    heute = date(2026, 3, 1)
    assert formate.datum_lesen("heute", heute) == heute
    assert formate.datum_lesen("Gestern", heute) == date(2026, 2, 28)
    assert formate.datum_lesen("2026-11-22", heute) == date(2026, 11, 22)
    assert formate.datum_lesen("22.11.2026", heute) == date(2026, 11, 22)
    with pytest.raises(ValueError):
        formate.datum_lesen("morgen", heute)


def test_ausgabeformate():
    assert formate.betrag(Decimal("1234.5")) == "1.234,50 €"
    assert formate.betrag(Decimal("-0.004")) == "0,00 €"
    assert formate.betrag(Decimal("-12.345")) == "-12,35 €"
    assert formate.betrag(Decimal("1000000")) == "1.000.000,00 €"
    assert formate.zahl(Decimal("1.50")) == "1,5"
    assert formate.zahl(Decimal("100")) == "100"
    assert formate.zahl(Decimal("1E+1")) == "10"
    assert formate.datum(date(2026, 1, 5)) == "05.01.2026"
    assert formate.dateiname("RE/2026 001") == "RE_2026_001"
