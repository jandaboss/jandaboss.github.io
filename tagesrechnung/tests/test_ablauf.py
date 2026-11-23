from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tagesrechnung import pdf as pdf_modul
from tagesrechnung.ablauf import ausfuehren
from tagesrechnung.rechnungsbuch import Rechnungsbuch

from .conftest import FalscherVersand, zeile

BERLIN = ZoneInfo("Europe/Berlin")
TAG = date(2026, 11, 22)
ABEND = datetime(2026, 11, 22, 23, 55, tzinfo=BERLIN)


def standardbestellungen(projekt):
    return projekt.bestellungen(
        zeile(1, "22.11.2026 09:00", kunde="K1", preis="49,95"),
        zeile(1, "22.11.2026 09:00", kunde="K1", artikel="Versand", preis="4,90"),
        zeile(2, "22.11.2026 12:00", kunde="K2", name="Max", email="max@example.com", preis="589,00"),
        zeile(3, "22.11.2026 15:00", kunde="K1", preis="12,90", satz="7", menge="2"),
        zeile(4, "21.11.2026 18:00", kunde="K1", preis="1,00"),
    )


def rechnungen(konfig):
    buch = Rechnungsbuch.oeffnen(konfig.speicher.datenbank)
    try:
        return buch.alle()
    finally:
        buch.schliessen()


def test_tageslauf_erstellt_und_versendet_sammelrechnungen(projekt, versand):
    konfig = standardbestellungen(projekt).konfig()
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)

    assert bericht.erfolgreich
    assert bericht.erstellt == ["RE-2026-00001", "RE-2026-00002"]
    assert bericht.versendet == bericht.erstellt

    erika, max_ = rechnungen(konfig)
    assert (erika.kunde_email, erika.bestellnummern, erika.brutto) == ("erika@example.com", ("1", "3"),
                                                                       Decimal("80.65"))
    assert (max_.kunde_email, max_.bestellnummern, max_.brutto) == ("max@example.com", ("2",), Decimal("589.00"))
    assert erika.status == max_.status == "versendet"
    assert erika.leistungsdatum == erika.rechnungsdatum == TAG
    assert erika.faellig == date(2026, 12, 6)

    pdf = konfig.speicher.archiv / "2026" / "RE-2026-00001.pdf"
    assert pdf.read_bytes().startswith(b"%PDF")

    nachricht = versand.nachrichten[0]
    assert nachricht["Subject"] == "Ihre Rechnung RE-2026-00001 vom 22.11.2026"
    assert "80,65 €" in nachricht.get_body(("plain",)).get_content()
    (anhang,) = nachricht.iter_attachments()
    assert anhang.get_filename() == "Rechnung_RE-2026-00001.pdf"
    assert anhang.get_content() == pdf.read_bytes()


def test_zweiter_lauf_rechnet_nichts_doppelt_ab(projekt, versand):
    konfig = standardbestellungen(projekt).konfig()
    ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)

    zweiter = FalscherVersand()
    bericht = ausfuehren(konfig, TAG, versand=zweiter, jetzt=ABEND)
    assert bericht.erfolgreich
    assert bericht.erstellt == [] and zweiter.nachrichten == []
    assert bericht.bereits_abgerechnet == 3


def test_spaete_bestellung_wird_nachgereicht_mit_naechster_nummer(projekt, versand):
    konfig = standardbestellungen(projekt).konfig()
    ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)

    # Kundin bestellt um 23:58 noch einmal – nach dem Lauf
    projekt.bestellungen(*projekt.zeilen, zeile(5, "22.11.2026 23:58", kunde="K1", preis="5,00"))
    bericht = ausfuehren(konfig, date(2026, 11, 23), nachholen_tage=1, versand=versand,
                         jetzt=datetime(2026, 11, 23, 23, 55, tzinfo=BERLIN))
    assert bericht.erstellt == ["RE-2026-00003"]
    neu = rechnungen(konfig)[-1]
    assert neu.bestellnummern == ("5",)
    assert neu.leistungsdatum == TAG
    assert neu.rechnungsdatum == date(2026, 11, 23)


def test_nachholen_rechnet_vortage_ab(projekt, versand):
    konfig = standardbestellungen(projekt).konfig()
    bericht = ausfuehren(konfig, TAG, nachholen_tage=1, versand=versand, jetzt=ABEND)
    assert len(bericht.erstellt) == 3
    # Bestellung 4 vom Vortag bekommt eine eigene Rechnung (anderer Leistungstag)
    assert [r.bestellnummern for r in rechnungen(konfig)] == [("4",), ("1", "3"), ("2",)]


def test_fehlgeschlagener_versand_wird_spaeter_mit_gleicher_nummer_wiederholt(projekt):
    konfig = standardbestellungen(projekt).konfig()
    gestoert = FalscherVersand(fehler_fuer={"max@example.com"})
    bericht = ausfuehren(konfig, TAG, versand=gestoert, jetzt=ABEND)

    assert not bericht.erfolgreich
    assert bericht.fehlgeschlagen == [("RE-2026-00002", "550 Postfach nicht erreichbar")]
    offen = [r for r in rechnungen(konfig) if r.status == "erstellt"]
    assert [(r.nummer, r.versuche, r.letzter_fehler) for r in offen] == [
        ("RE-2026-00002", 1, "550 Postfach nicht erreichbar")
    ]

    repariert = FalscherVersand()
    bericht = ausfuehren(konfig, TAG, versand=repariert, jetzt=ABEND)
    assert bericht.erfolgreich
    assert bericht.erstellt == []
    assert bericht.versendet == ["RE-2026-00002"]
    assert repariert.nachrichten[0]["To"].addresses[0].addr_spec == "max@example.com"
    assert all(r.status == "versendet" for r in rechnungen(konfig))


def test_probelauf_aendert_nichts(projekt):
    konfig = standardbestellungen(projekt).konfig()
    bericht = ausfuehren(konfig, TAG, probelauf=True, jetzt=ABEND)

    assert bericht.erstellt == ["RE-2026-00001", "RE-2026-00002"]
    assert not konfig.speicher.datenbank.exists()
    assert not konfig.speicher.archiv.exists()
    assert (konfig.speicher.probelauf / "2026" / "RE-2026-00001.pdf").is_file()
    assert (konfig.speicher.probelauf / "RE-2026-00001.eml").is_file()

    # Danach echter Lauf: gleiche Nummern, Probelauf hat nichts verbraucht
    versand = FalscherVersand()
    assert ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND).erstellt == ["RE-2026-00001", "RE-2026-00002"]

    # Probelauf auf bestehendem Rechnungsbuch zählt korrekt weiter, ohne es zu ändern
    projekt.bestellungen(*projekt.zeilen, zeile(9, "22.11.2026 23:59", kunde="K9", email="neu@example.com"))
    vorher = konfig.speicher.datenbank.read_bytes()
    assert ausfuehren(konfig, TAG, probelauf=True, jetzt=ABEND).erstellt == ["RE-2026-00003"]
    assert konfig.speicher.datenbank.read_bytes() == vorher


def test_eine_rechnung_pro_bestellung(projekt, versand):
    konfig = standardbestellungen(projekt).einstellung("rechnung", 'eine_rechnung_pro = "bestellung"').konfig()
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    assert len(bericht.erstellt) == 3
    assert [r.bestellnummern for r in rechnungen(konfig)] == [("1",), ("2",), ("3",)]


def test_nummernkreis_beginnt_jedes_jahr_neu(projekt, versand):
    konfig = projekt.bestellungen(
        zeile(1, "31.12.2026 10:00"), zeile(2, "01.01.2027 10:00")
    ).einstellung("rechnung", "startnummer = 100").konfig()
    ausfuehren(konfig, date(2026, 12, 31), versand=versand, jetzt=datetime(2026, 12, 31, 23, 55, tzinfo=BERLIN))
    ausfuehren(konfig, date(2027, 1, 1), versand=versand, jetzt=datetime(2027, 1, 1, 23, 55, tzinfo=BERLIN))
    assert [r.nummer for r in rechnungen(konfig)] == ["RE-2026-00100", "RE-2027-00100"]
    assert (konfig.speicher.archiv / "2027" / "RE-2027-00100.pdf").is_file()


def test_jahresuebergreifender_nummernkreis(projekt, versand):
    konfig = projekt.bestellungen(zeile(1, "31.12.2026"), zeile(2, "01.01.2027")).einstellung(
        "rechnung", 'nummer_format = "R{nummer:04d}"\nnummernkreis_pro_jahr = false'
    ).konfig()
    ausfuehren(konfig, date(2026, 12, 31), versand=versand, jetzt=datetime(2026, 12, 31, 23, 55, tzinfo=BERLIN))
    ausfuehren(konfig, date(2027, 1, 1), versand=versand, jetzt=datetime(2027, 1, 1, 23, 55, tzinfo=BERLIN))
    assert [r.nummer for r in rechnungen(konfig)] == ["R0001", "R0002"]


def test_pdf_fehler_verbraucht_keine_nummer(projekt, versand, monkeypatch):
    konfig = standardbestellungen(projekt).konfig()
    original = pdf_modul.erzeugen
    aufrufe = []

    def wackelig(rechnung, konfig_, ziel):
        aufrufe.append(rechnung.nummer)
        if len(aufrufe) == 1:
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ziel.write_bytes(b"halb geschrieben")
            raise OSError("Datenträger voll")
        original(rechnung, konfig_, ziel)

    monkeypatch.setattr(pdf_modul, "erzeugen", wackelig)
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)

    assert len(bericht.datenfehler) == 1 and "Datenträger voll" in bericht.datenfehler[0]
    # Die Nummer der gescheiterten Rechnung wird für die nächste wiederverwendet → keine Lücke
    assert aufrufe == ["RE-2026-00001", "RE-2026-00001"]
    assert bericht.erstellt == ["RE-2026-00001"]
    assert [r.bestellnummern for r in rechnungen(konfig)] == [("2",)]

    # Nächster Lauf holt die gescheiterte Bestellung nach
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    assert bericht.erstellt == ["RE-2026-00002"]
    assert [r.bestellnummern for r in rechnungen(konfig)] == [("2",), ("1", "3")]


def test_vergebene_nummer_ueberschreibt_kein_archiv_pdf(projekt, versand):
    konfig = projekt.bestellungen(zeile(1, "22.11.2026")).konfig()
    ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    archiviert = konfig.speicher.archiv / "2026" / "RE-2026-00001.pdf"
    inhalt = archiviert.read_bytes()

    # Jemand stellt den Nummernkreis falsch um → Nummer wäre doppelt
    projekt.bestellungen(zeile(1, "22.11.2026"), zeile(2, "22.11.2026", kunde="K2", email="max@example.com"))
    projekt.extra["rechnung"] = 'nummer_format = "RE-2026-{nummer:05d}"\nnummernkreis_pro_jahr = false'
    konfig = projekt.konfig()
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)

    assert bericht.erstellt == []
    assert "bereits vergeben" in bericht.datenfehler[0]
    assert archiviert.read_bytes() == inhalt


def test_datenfehler_blockieren_nur_die_betroffene_bestellung(projekt, versand):
    konfig = projekt.bestellungen(
        zeile(1, "22.11.2026", preis="??"),
        zeile(2, "22.11.2026", kunde="K2", email="max@example.com"),
    ).konfig()
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    assert not bericht.erfolgreich
    assert len(bericht.datenfehler) == 1
    assert [r.bestellnummern for r in rechnungen(konfig)] == [("2",)]


def test_fehlende_quelle_wird_gemeldet_offene_trotzdem_versendet(projekt):
    konfig = projekt.bestellungen(zeile(1, "22.11.2026")).konfig()
    ausfuehren(konfig, TAG, versand=FalscherVersand(fehler_fuer={"erika@example.com"}), jetzt=ABEND)
    projekt.csv.unlink()

    versand = FalscherVersand()
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    assert bericht.versendet == ["RE-2026-00001"]
    assert "nicht gefunden" in bericht.datenfehler[0]


@pytest.mark.parametrize("anzahl", [60])
def test_lange_rechnung_mehrseitig(projekt, versand, anzahl):
    pypdf = pytest.importorskip("pypdf")
    konfig = projekt.bestellungen(
        *[zeile(1, "22.11.2026 10:00", artikel=f"Artikel Nummer {i}", preis="1,00") for i in range(anzahl)]
    ).konfig()
    ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    leser = pypdf.PdfReader(konfig.speicher.archiv / "2026" / "RE-2026-00001.pdf")
    assert len(leser.pages) >= 2
    letzte = leser.pages[-1].extract_text()
    assert f"Seite {len(leser.pages)} von {len(leser.pages)}" in letzte
    assert "Gesamtbetrag" in letzte and "60,00 €" in letzte
