import smtplib
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tagesrechnung import cli
from tagesrechnung.ablauf import ausfuehren
from tagesrechnung.versand import SmtpVersand, VersandFehler, probenachricht

from .conftest import zeile

BERLIN = ZoneInfo("Europe/Berlin")
TAG = date(2026, 11, 22)
ABEND = datetime(2026, 11, 22, 23, 55, tzinfo=BERLIN)


def pdf_text(pfad) -> str:
    pypdf = pytest.importorskip("pypdf")
    return "\n".join(seite.extract_text() for seite in pypdf.PdfReader(pfad).pages)


def test_pdf_enthaelt_pflichtangaben(projekt, versand):
    konfig = projekt.bestellungen(
        zeile(1001, "22.11.2026 11:20", kunde="K-7", name="Max <Mustermann> & Co", firma="Bau & Söhne GmbH",
              ust_idnr="DE999999999", artikel="Siebträger „Pro“", preis="589,00"),
        zeile(1001, "22.11.2026 11:20", kunde="K-7", name="Max <Mustermann> & Co", firma="Bau & Söhne GmbH",
              ust_idnr="DE999999999", artikel="Kaffee", preis="24,90", satz="7", menge="3"),
    ).konfig()
    ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    text = pdf_text(konfig.speicher.archiv / "2026" / "RE-2026-00001.pdf")

    for erwartet in [
        "Test GmbH", "Teststraße 1", "12345 Teststadt",                      # leistender Unternehmer
        "Steuernummer 12/345/67890",                                         # Steuernummer
        "Bau & Söhne GmbH", "Max <Mustermann> & Co", "10115 Berlin",         # Empfänger (HTML-sicher)
        "Rechnung RE-2026-00001", "22.11.2026",                              # Nummer, Datum
        "Liefer-/Leistungsdatum",
        "Siebträger „Pro“", "Kaffee", "3 Stk.",                              # Menge und Art
        "zzgl. 19 % USt. auf 494,96 €", "94,04 €",                           # Entgelt und Steuer je Satz
        "zzgl. 7 % USt. auf 69,81 €", "4,89 €",
        "Gesamtbetrag", "663,70 €",
        "DE999999999", "K-7", "Bestellung 1001",
    ]:
        assert erwartet in text, erwartet


def test_laenderangabe_nur_bei_auslandsanschrift(projekt, versand):
    zeilen = [zeile(1, "22.11.2026", kunde="K1"), zeile(2, "22.11.2026", kunde="K2", email="b@example.com")]
    zeilen = [zeilen[0].replace("Berlin;;", "Berlin;DE;"), zeilen[1].replace("Berlin;;", "Berlin;Österreich;")]
    konfig = projekt.bestellungen(*zeilen).konfig()
    ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    assert "DE" not in [z.strip() for z in pdf_text(konfig.speicher.archiv / "2026" / "RE-2026-00001.pdf").splitlines()]
    assert "Österreich" in pdf_text(konfig.speicher.archiv / "2026" / "RE-2026-00002.pdf")


def test_pdf_kleinunternehmer(projekt, versand):
    konfig = projekt.bestellungen(zeile(1, "22.11.2026", preis="100,00")).einstellung(
        "rechnung", "kleinunternehmer = true\nzahlungsziel_tage = 0"
    ).konfig()
    ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    text = pdf_text(konfig.speicher.archiv / "2026" / "RE-2026-00001.pdf")
    assert "Steuerbefreiung für Kleinunternehmer gemäß § 19 UStG – es wird keine Umsatzsteuer berechnet." in text
    assert "USt." not in text.replace("UStG", "")
    assert "Zahlbar bis" not in text


class FalscherSmtp:
    instanzen: list["FalscherSmtp"] = []
    login_fehler = False

    def __init__(self, server, port, timeout=None, context=None):
        self.server, self.port = server, port
        self.ablauf: list = []
        FalscherSmtp.instanzen.append(self)

    def starttls(self, context=None):
        self.ablauf.append("starttls")

    def login(self, benutzer, passwort):
        if FalscherSmtp.login_fehler:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
        self.ablauf.append(("login", benutzer, passwort))

    def send_message(self, nachricht):
        self.ablauf.append(("send", nachricht["To"]))

    def quit(self):
        self.ablauf.append("quit")

    def close(self):
        pass


@pytest.fixture
def smtp(monkeypatch):
    FalscherSmtp.instanzen = []
    FalscherSmtp.login_fehler = False
    monkeypatch.setattr(smtplib, "SMTP", FalscherSmtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FalscherSmtp)
    return FalscherSmtp


def test_smtp_versand_mit_starttls_und_passwort_aus_umgebung(projekt, smtp, monkeypatch):
    monkeypatch.setenv("TAGESRECHNUNG_SMTP_PASSWORT", "geheim")
    konfig = projekt.einstellung("email", 'benutzer = "rechnung@test.example"').konfig()
    with SmtpVersand(konfig.email) as versand:
        versand.senden(probenachricht(konfig, "a@example.com"), "1")
        versand.senden(probenachricht(konfig, "b@example.com"), "2")
    (verbindung,) = smtp.instanzen  # eine Verbindung für alle Mails
    assert (verbindung.server, verbindung.port) == ("smtp.test.example", 587)
    assert verbindung.ablauf == [
        "starttls", ("login", "rechnung@test.example", "geheim"),
        ("send", "a@example.com"), ("send", "b@example.com"), "quit",
    ]


def test_smtp_ohne_passwort(projekt, smtp, monkeypatch):
    monkeypatch.delenv("TAGESRECHNUNG_SMTP_PASSWORT", raising=False)
    konfig = projekt.einstellung("email", 'benutzer = "rechnung@test.example"').konfig()
    with SmtpVersand(konfig.email) as versand, pytest.raises(VersandFehler, match="Kein SMTP-Passwort"):
        versand.senden(probenachricht(konfig, "a@example.com"), "1")


def test_smtp_anmeldefehler_wird_nicht_fuer_jede_mail_wiederholt(projekt, smtp):
    smtp.login_fehler = True
    konfig = projekt.einstellung("email", 'benutzer = "x"\npasswort = "falsch"').konfig()
    with SmtpVersand(konfig.email) as versand:
        for _ in range(3):
            with pytest.raises(VersandFehler, match="Anmeldung am SMTP-Server fehlgeschlagen"):
                versand.senden(probenachricht(konfig, "a@example.com"), "1")
    assert len(smtp.instanzen) == 1


def test_bcc_und_antwortadresse(projekt, versand):
    konfig = projekt.bestellungen(zeile(1, "22.11.2026")).einstellung(
        "email", 'bcc = ["buchhaltung@test.example"]\nantwort_an = "service@test.example"'
    ).konfig()
    ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)
    (nachricht,) = versand.nachrichten
    assert nachricht["Bcc"] == "buchhaltung@test.example"
    assert nachricht["Reply-To"] == "service@test.example"
    assert nachricht["From"] == "Test GmbH <rechnung@test.example>"


def test_cli_probelauf_und_liste(projekt, capsys):
    projekt.bestellungen(zeile(1, "22.11.2026"), zeile(2, "22.11.2026", kunde="K2", email="max@example.com"))
    projekt.konfig()
    assert cli.main(["ausfuehren", "-c", str(projekt.config), "--datum", "22.11.2026", "--probelauf"]) == 0
    assert len(list((projekt.ordner / "probelauf").glob("*.eml"))) == 2

    assert cli.main(["liste", "-c", str(projekt.config)]) == 0
    assert "Noch keine Rechnungen" in capsys.readouterr().out


def test_cli_fehlercodes(projekt, tmp_path):
    assert cli.main(["ausfuehren", "-c", str(tmp_path / "fehlt.toml")]) == 2
    projekt.bestellungen(zeile(1, "22.11.2026"))
    projekt.konfig()
    assert cli.main(["ausfuehren", "-c", str(projekt.config), "--datum", "übermorgen"]) == 2
    # Datenfehler → Rückgabewert 1, damit Cron/Systemd Alarm schlagen kann
    projekt.bestellungen(zeile(1, "22.11.2026", preis="kaputt"))
    assert cli.main(["ausfuehren", "-c", str(projekt.config), "--datum", "22.11.2026", "--probelauf"]) == 1


def test_beispielkonfiguration_ist_gueltig():
    from pathlib import Path

    from tagesrechnung.konfiguration import laden

    konfig = laden(Path(__file__).parent.parent / "config.beispiel.toml")
    assert konfig.quelle.pfad.is_file()
