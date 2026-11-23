import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from tagesrechnung import quellen
from tagesrechnung.konfiguration import KonfigurationsFehler

from .conftest import KOPF, zeile

TAG = date(2026, 11, 22)


def laden(konfig, von=TAG, bis=TAG):
    return quellen.laden(konfig.quelle, von, bis, konfig.speicher.zeitzone, konfig.rechnung.standard_ust_satz)


def test_csv_wird_nach_datum_gefiltert(projekt):
    konfig = projekt.bestellungen(
        zeile(1, "21.11.2026 23:59"),
        zeile(2, "22.11.2026 00:00"),
        zeile(3, "2026-11-22T22:59:59Z"),  # = 23:59:59 in Berlin
        zeile(4, "2026-11-22T23:00:00Z"),  # = 00:00 am 23.11. in Berlin
    ).konfig()
    ergebnis = laden(konfig)
    assert not ergebnis.fehler
    assert [p.bestellnummer for p in ergebnis.positionen] == ["2", "3"]


def test_csv_werte_werden_gelesen(projekt):
    konfig = projekt.bestellungen(zeile(7, "22.11.2026 10:00", menge="1,5", preis="1.234,50", satz="7 %")).konfig()
    (p,) = laden(konfig).positionen
    assert (p.menge, p.einzelpreis, p.ust_satz) == (Decimal("1.5"), Decimal("1234.50"), Decimal("7"))
    assert p.kunde.anschrift() == ["Erika Musterfrau", "Weg 1", "10115 Berlin"]


def test_fehlerhafte_zeile_sperrt_ganze_bestellung(projekt):
    konfig = projekt.bestellungen(
        zeile(1, "22.11.2026", preis="10,00"),
        zeile(1, "22.11.2026", preis="zehn"),
        zeile(2, "22.11.2026", email="keine-adresse"),
        zeile(3, "22.11.2026"),
        zeile(4, "irgendwann"),
    ).konfig()
    ergebnis = laden(konfig)
    assert ergebnis.fehlerhafte_bestellungen == {"1", "2", "4"}
    assert len(ergebnis.fehler) == 3
    assert "Zeile 3" in ergebnis.fehler[0]


def test_fehlerhafte_alte_zeilen_stoeren_nicht(projekt):
    konfig = projekt.bestellungen(zeile(1, "01.01.2025", preis="kaputt"), zeile(2, "22.11.2026")).konfig()
    ergebnis = laden(konfig)
    assert not ergebnis.fehler
    assert [p.bestellnummer for p in ergebnis.positionen] == ["2"]


def test_standard_steuersatz_wenn_spalte_leer(projekt):
    konfig = projekt.bestellungen(zeile(1, "22.11.2026", satz="")).einstellung(
        "rechnung", "standard_ust_satz = 7"
    ).konfig()
    assert laden(konfig).positionen[0].ust_satz == Decimal("7")


def test_eigene_spaltennamen_und_komma_als_trenner(projekt):
    projekt.csv.write_text(
        "Order ID,Datum,Name,E-Mail,Produkt,Anzahl,Preis\n"
        '"A-1",2026-11-22 12:00,"Musterfrau, Erika",erika@example.com,"Tee, grün",2,4.50\n',
        encoding="utf-8",
    )
    konfig = projekt.einstellung(
        "quelle",
        """
        [quelle.spalten]
        bestellnummer = "Order ID"
        bestelldatum = "datum"
        kunde_name = "Name"
        kunde_email = "E-Mail"
        artikel = "Produkt"
        menge = "Anzahl"
        einzelpreis = "Preis"
        """,
    ).konfig()
    (p,) = laden(konfig).positionen
    assert (p.bestellnummer, p.kunde.name, p.artikel, p.einzelpreis) == ("A-1", "Musterfrau, Erika", "Tee, grün",
                                                                         Decimal("4.50"))
    assert p.kunde.schluessel == "erika@example.com"  # ohne Kundennummer zählt die E-Mail-Adresse
    assert p.ust_satz == Decimal("19")


def test_fehlende_pflichtspalte(projekt):
    projekt.csv.write_text("bestellnummer;bestelldatum\n1;22.11.2026\n", encoding="utf-8")
    konfig = projekt.konfig()
    with pytest.raises(quellen.QuellenFehler, match="Pflichtspalten fehlen: kunde_name, kunde_email"):
        laden(konfig)


def test_fehlende_datei(projekt):
    konfig = projekt.konfig()
    with pytest.raises(quellen.QuellenFehler, match="nicht gefunden"):
        laden(konfig)


def test_windows_kodierung(projekt):
    projekt.csv.write_bytes((KOPF + "\n" + zeile(1, "22.11.2026", name="Jürgen Müller") + "\n").encode("cp1252"))
    konfig = projekt.einstellung("quelle", 'kodierung = "cp1252"').konfig()
    assert laden(konfig).positionen[0].kunde.name == "Jürgen Müller"

    projekt.extra["quelle"] = ""  # Standard utf-8 passt nicht → verständliche Fehlermeldung
    konfig = projekt.konfig()
    with pytest.raises(quellen.QuellenFehler, match="cp1252"):
        laden(konfig)


def test_sqlite_quelle(projekt):
    db = projekt.ordner / "shop.sqlite"
    with sqlite3.connect(db) as verbindung:
        verbindung.execute(
            "CREATE TABLE pos (nr TEXT, zeit TEXT, name TEXT, mail TEXT, artikel TEXT, menge REAL, preis REAL)"
        )
        verbindung.executemany(
            "INSERT INTO pos VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("1", "2026-11-21 10:00:00", "Erika", "erika@example.com", "Tee", 1, 3.5),
                ("2", "2026-11-22 10:00:00", "Erika", "erika@example.com", "Kaffee", 2, 24.9),
                ("3", "2026-11-22 23:59:00", "Max", "max@example.com", "Kanne", 1, 19.99),
            ],
        )
    verbindung.close()
    projekt.einstellung(
        "quelle",
        """
        [quelle.spalten]
        bestellnummer = "nr"
        bestelldatum = "zeit"
        kunde_name = "name"
        kunde_email = "mail"
        einzelpreis = "preis"
        """,
    )
    konfig = projekt.konfig()
    konfig.quelle.typ = "sqlite"
    konfig.quelle.pfad = db
    konfig.quelle.abfrage = "SELECT * FROM pos WHERE date(zeit) BETWEEN :von AND :bis"
    ergebnis = laden(konfig)
    assert [(p.bestellnummer, p.einzelpreis) for p in ergebnis.positionen] == [
        ("2", Decimal("24.9")),
        ("3", Decimal("19.99")),
    ]


def test_sqlite_ohne_abfrage_ist_konfigurationsfehler(projekt):
    projekt.extra["quelle"] = ""
    projekt.konfig()  # gültig
    text = projekt.config.read_text().replace('typ = "csv"', 'typ = "sqlite"')
    projekt.config.write_text(text)
    from tagesrechnung.konfiguration import laden as konfig_laden

    with pytest.raises(KonfigurationsFehler, match="abfrage"):
        konfig_laden(projekt.config)
