"""Abweichende Rechnungsanschriften und Käuferhinweise."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tagesrechnung import anschriften, cli, ebay
from tagesrechnung.ablauf import ausfuehren
from tagesrechnung.konfiguration import EbayEinstellungen
from tagesrechnung.rechnungsbuch import Rechnungsbuch

from .conftest import FalscherVersand, zeile
from .test_ebay import FalschesEbay, bestellung, verbunden

BERLIN = ZoneInfo("Europe/Berlin")
TAG = date(2026, 11, 22)
ABEND = datetime(2026, 11, 22, 23, 55, tzinfo=BERLIN)
HINWEIS = ("Guten Morgen, bitte stellen Sie die Rechnung zwingend auf die Fa. Beispiel Facility GmbH, "
           "Musterallee 80-82, 70191 Stuttgart aus, damit abgerechnet werden kann. Gruß Erika Musterfrau")
FIRMA = anschriften.Anschrift(firma="Beispiel Facility GmbH", strasse="Musterallee 80-82", plz="70191",
                              ort="Stuttgart")


@pytest.fixture
def server(monkeypatch):
    falsch = FalschesEbay()
    monkeypatch.setattr(ebay, "_http", falsch)
    return falsch


def rechnungen(konfig):
    buch = Rechnungsbuch.oeffnen(konfig.speicher.datenbank)
    try:
        return buch.alle()
    finally:
        buch.schliessen()


# --- Datei -----------------------------------------------------------------------------------------


def test_eintragen_und_laden(tmp_path):
    pfad = tmp_path / "rechnungsanschriften.toml"
    assert anschriften.laden(pfad) == {}
    sonderzeichen = anschriften.Anschrift(firma='Müller "&" Söhne GmbH', name="", strasse="Weg 1\\2",
                                          plz="1010", ort="Wien", land="Österreich", ust_idnr="ATU12345678")
    anschriften.eintragen(pfad, "12-11111-11111", FIRMA)
    anschriften.eintragen(pfad, "12-22222-22222", anschriften.Anschrift(wie_bestellt=True))
    anschriften.eintragen(pfad, "12-33333-33333", sonderzeichen)

    geladen = anschriften.laden(pfad)
    assert geladen == {
        "12-11111-11111": FIRMA,
        "12-22222-22222": anschriften.Anschrift(wie_bestellt=True),
        "12-33333-33333": sonderzeichen,
    }
    assert pfad.read_text(encoding="utf-8").startswith("# Abweichende Rechnungsanschriften")

    with pytest.raises(anschriften.AnschriftenFehler, match="schon etwas eingetragen"):
        anschriften.eintragen(pfad, "12-11111-11111", FIRMA)


@pytest.mark.parametrize(
    "inhalt, meldung",
    [
        ('["1"]\nfirma = "X GmbH"\nplz = "1"\nort = "Y"\n', "es fehlt strasse"),
        ('["1"]\nstrasse = "A"\nplz = "1"\nort = "Y"\n', "firma oder name"),
        ('["1"]\nfirma = "X"\nstrasse = "A"\nplz = 70191\nort = "Y"\n', "Text in Anführungszeichen"),
        ('["1"]\nfirma = "X"\nstrase = "A"\n', "unbekannte Angabe 'strase'"),
        ('["1"\n', "ungültiges Format"),
    ],
)
def test_fehlerhafte_datei(tmp_path, inhalt, meldung):
    pfad = tmp_path / "a.toml"
    pfad.write_text(inhalt, encoding="utf-8")
    with pytest.raises(anschriften.AnschriftenFehler, match=meldung):
        anschriften.laden(pfad)


def test_name_bleibt_ohne_angabe_erhalten():
    from tagesrechnung.modelle import Kunde

    kunde = Kunde(schluessel="ebay:erika", kunden_nr="", name="Erika Musterfrau", email="x@members.ebay.com",
                  strasse="Privatweg 1", plz="10115", ort="Berlin")
    neu = FIRMA.anwenden(kunde, "12-1")
    assert neu.anschrift() == ["Beispiel Facility GmbH", "Erika Musterfrau", "Musterallee 80-82", "70191 Stuttgart"]
    assert neu.email == kunde.email and neu.schluessel == "ebay:erika#12-1"


# --- eBay-Käuferhinweise -----------------------------------------------------------------------------


def einst():
    return EbayEinstellungen(client_id="x", ru_name="y", token_datei=None)


def test_hinweis_zur_rechnung_haelt_bestellung_an():
    roh = bestellung()
    roh["buyerCheckoutNotes"] = HINWEIS
    b = ebay.auswerten(roh, einst(), BERLIN, Decimal(19))
    assert b.fehler and not b.abrechenbar
    assert "Käuferhinweis zur Rechnung" in b.grund and "tagesrechnung anschrift 12-11111-11111" in b.grund

    b = ebay.auswerten(roh, einst(), BERLIN, Decimal(19), freigegeben={"12-11111-11111"})
    assert b.abrechenbar and b.hinweis == HINWEIS


def test_anderer_hinweis_haelt_nicht_an():
    roh = bestellung()
    roh["buyerCheckoutNotes"] = "Bitte als Geschenk verpacken, danke!"
    b = ebay.auswerten(roh, einst(), BERLIN, Decimal(19))
    assert b.abrechenbar and b.hinweis.startswith("Bitte als Geschenk")


def test_unbezahlte_bestellung_mit_hinweis_bleibt_unbezahlt():
    roh = bestellung(zahlstatus="PENDING")
    roh["buyerCheckoutNotes"] = HINWEIS
    b = ebay.auswerten(roh, einst(), BERLIN, Decimal(19))
    assert b.grund == "noch nicht bezahlt" and not b.fehler


# --- Tageslauf ---------------------------------------------------------------------------------------


def test_ebay_bestellung_mit_firmenwunsch(projekt, server):
    konfig = verbunden(projekt, server)
    mit_hinweis = bestellung("12-00001-00001", "2026-11-22T08:00:00.000Z")
    mit_hinweis["buyerCheckoutNotes"] = HINWEIS
    server.bestellungen = [mit_hinweis, bestellung("12-00002-00002", "2026-11-22T09:00:00.000Z")]

    # 1. Lauf: die zweite Bestellung desselben Käufers wird abgerechnet, die mit Hinweis angehalten
    bericht = ausfuehren(konfig, TAG, versand=FalscherVersand(), jetzt=ABEND)
    assert bericht.erstellt == ["RE-2026-00001"]
    assert not bericht.erfolgreich and "Käuferhinweis zur Rechnung" in bericht.datenfehler[0]
    assert rechnungen(konfig)[0].bestellnummern == ("12-00002-00002",)

    # Anschrift eintragen → 2. Lauf rechnet auf die Firma ab
    c = ["-c", str(projekt.config)]
    assert cli.main(["anschrift", "12-00001-00001", *c, "--firma", "Beispiel Facility GmbH",
                     "--strasse", "Musterallee 80-82", "--plz", "70191", "--ort", "Stuttgart"]) == 0
    versand = FalscherVersand()
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    assert bericht.erfolgreich, bericht.datenfehler
    assert bericht.erstellt == ["RE-2026-00002"]

    pypdf = pytest.importorskip("pypdf")
    text = pypdf.PdfReader(konfig.speicher.archiv / "2026" / "RE-2026-00002.pdf").pages[0].extract_text()
    assert "Beispiel Facility GmbH" in text and "Musterallee 80-82" in text and "70191 Stuttgart" in text
    assert "Erika Musterfrau" in text and "Beispielweg 5" not in text
    # Die Mail geht weiterhin über eBay an den Käufer
    assert versand.nachrichten[0]["To"].addresses[0].addr_spec.endswith("@members.ebay.com")


def test_freigabe_wie_bestellt(projekt, server):
    konfig = verbunden(projekt, server)
    roh = bestellung("12-00001-00001", "2026-11-22T08:00:00.000Z")
    roh["buyerCheckoutNotes"] = "Brauche die Rechnung schnell, danke"
    server.bestellungen = [roh]
    assert ausfuehren(konfig, TAG, versand=FalscherVersand(), jetzt=ABEND).erstellt == []

    assert cli.main(["anschrift", "12-00001-00001", "-c", str(projekt.config), "--wie-bestellt"]) == 0
    bericht = ausfuehren(konfig, TAG, versand=FalscherVersand(), jetzt=ABEND)
    assert bericht.erstellt == ["RE-2026-00001"] and bericht.erfolgreich


def test_abweichende_anschrift_auch_bei_csv(projekt, versand):
    konfig = projekt.bestellungen(zeile(1, "22.11.2026 10:00"), zeile(2, "22.11.2026 11:00")).konfig()
    anschriften.eintragen(konfig.rechnung.anschriften_datei, "2", FIRMA)
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    # gleiche Kundin, aber Bestellung 2 geht an die Firma → zwei getrennte Rechnungen
    assert [r.bestellnummern for r in rechnungen(konfig)] == [("1",), ("2",)]
    assert bericht.erfolgreich


def test_kaputte_anschriftendatei_stoppt_neue_rechnungen(projekt, versand):
    konfig = projekt.bestellungen(zeile(1, "22.11.2026")).konfig()
    konfig.rechnung.anschriften_datei.write_text('["1"\n', encoding="utf-8")
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    assert bericht.erstellt == [] and "ungültiges Format" in bericht.datenfehler[0]


def test_cli_anschrift_warnt_bei_bereits_abgerechneter_bestellung(projekt, versand):
    konfig = projekt.bestellungen(zeile(1, "22.11.2026")).konfig()
    ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    c = ["-c", str(projekt.config)]
    assert cli.main(["anschrift", "1", *c, "--firma", "X GmbH", "--strasse", "A 1", "--plz", "1", "--ort", "B"]) == 1
    assert cli.main(["anschrift", "2", *c, "--wie-bestellt", "--firma", "X"]) == 2
    assert cli.main(["anschrift", "3", *c, "--firma", "X GmbH"]) == 2  # Straße/PLZ/Ort fehlen


def test_inhaber_auf_der_rechnung(projekt, versand):
    pypdf = pytest.importorskip("pypdf")
    projekt.konfig()
    projekt.config.write_text(projekt.config.read_text().replace(
        'name = "Test GmbH"', 'name = "Lampenladen Muster"\ninhaber = "Maria Muster"'))
    from tagesrechnung.konfiguration import laden

    konfig = laden(projekt.config)
    projekt.bestellungen(zeile(1, "22.11.2026"))
    ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    text = pypdf.PdfReader(konfig.speicher.archiv / "2026" / "RE-2026-00001.pdf").pages[0].extract_text()
    assert "Lampenladen Muster · Maria Muster · Teststraße 1" in text
    assert "Inh. Maria Muster" in text
