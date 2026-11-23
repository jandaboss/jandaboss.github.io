from datetime import datetime
from decimal import Decimal

from tagesrechnung.berechnung import bestellungen_bilden, gruppieren, summen_berechnen
from tagesrechnung.modelle import Kunde, Position

ERIKA = Kunde(schluessel="K1", kunden_nr="K1", name="Erika", email="erika@example.com")
MAX = Kunde(schluessel="K2", kunden_nr="K2", name="Max", email="max@example.com")


def pos(nr="1", zeit=datetime(2026, 11, 22, 10), kunde=ERIKA, menge="1", preis="10", satz="19"):
    return Position(
        bestellnummer=nr, bestellzeit=zeit, kunde=kunde, artikel="Artikel",
        menge=Decimal(menge), einzelpreis=Decimal(preis), ust_satz=Decimal(satz),
    )


def test_bruttopreise_gemischte_steuersaetze():
    summen = summen_berechnen(
        [pos(preis="589.00"), pos(menge="3", preis="24.90", satz="7")], "brutto", kleinunternehmer=False
    )
    assert summen.brutto == Decimal("663.70")
    assert [(g.satz, g.netto, g.steuer) for g in summen.gruppen] == [
        (Decimal("19"), Decimal("494.96"), Decimal("94.04")),
        (Decimal("7"), Decimal("69.81"), Decimal("4.89")),
    ]
    assert summen.netto + summen.steuer == summen.brutto


def test_nettopreise():
    summen = summen_berechnen([pos(menge="2", preis="10.00"), pos(preis="5.55", satz="7")], "netto", False)
    assert summen.netto == Decimal("25.55")
    assert [(g.satz, g.steuer) for g in summen.gruppen] == [(Decimal("19"), Decimal("3.80")),
                                                            (Decimal("7"), Decimal("0.39"))]
    assert summen.brutto == Decimal("29.74")


def test_steuer_wird_je_satz_und_nicht_je_position_gerundet():
    # 10 × 0,02 € netto bei 19 %: je Position gerundet wären es 10 × 0,00 €,
    # richtig ist 0,20 € × 19 % = 0,038 → 0,04 €
    summen = summen_berechnen([pos(preis="0.02") for _ in range(10)], "netto", False)
    assert summen.steuer == Decimal("0.04")


def test_kleinunternehmer_ohne_ust():
    summen = summen_berechnen([pos(preis="100"), pos(preis="50", satz="7")], "brutto", kleinunternehmer=True)
    assert summen.steuer == 0
    assert summen.netto == summen.brutto == Decimal("150.00")
    assert len(summen.gruppen) == 1


def test_rabattzeile_mit_negativem_preis():
    summen = summen_berechnen([pos(preis="119.00"), pos(preis="-11.90")], "brutto", False)
    assert summen.brutto == Decimal("107.10")
    assert summen.netto == Decimal("90.00")


def test_bestellungen_und_sammelrechnung_je_kunde_und_tag():
    positionen = [
        pos("1", datetime(2026, 11, 22, 9)),
        pos("1", datetime(2026, 11, 22, 9)),
        pos("2", datetime(2026, 11, 22, 8), kunde=MAX),
        pos("3", datetime(2026, 11, 22, 15)),
        pos("4", datetime(2026, 11, 21, 15)),  # anderer Tag → eigene Rechnung
    ]
    bestellungen, fehler = bestellungen_bilden(positionen)
    assert not fehler
    assert [b.nummer for b in bestellungen] == ["4", "2", "1", "3"]
    assert len(bestellungen[2].positionen) == 2

    je_kunde = gruppieren(bestellungen, "kunde")
    assert [[b.nummer for b in g] for g in je_kunde] == [["4"], ["2"], ["1", "3"]]

    je_bestellung = gruppieren(bestellungen, "bestellung")
    assert [[b.nummer for b in g] for g in je_bestellung] == [["4"], ["2"], ["1"], ["3"]]


def test_bestellung_mit_verschiedenen_kunden_wird_abgelehnt():
    bestellungen, fehler = bestellungen_bilden([pos("1"), pos("1", kunde=MAX), pos("2")])
    assert [b.nummer for b in bestellungen] == ["2"]
    assert "Bestellung 1" in fehler[0]
