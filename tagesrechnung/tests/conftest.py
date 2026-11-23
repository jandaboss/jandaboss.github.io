from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tagesrechnung.konfiguration import laden

KOPF = (
    "bestellnummer;bestelldatum;kunden_nr;kunde_name;kunde_firma;kunde_strasse;kunde_plz;kunde_ort;"
    "kunde_land;kunde_email;kunde_ust_idnr;artikelnummer;artikel;menge;einheit;einzelpreis;ust_satz"
)


def zeile(nr, datum, kunde="K1", name="Erika Musterfrau", email="erika@example.com", artikel="Kaffee",
          menge="1", preis="10,00", satz="19", firma="", ust_idnr=""):
    return (f"{nr};{datum};{kunde};{name};{firma};Weg 1;10115;Berlin;;{email};{ust_idnr};A-1;{artikel};"
            f"{menge};Stk.;{preis};{satz}")


class Projekt:
    """Ein Arbeitsordner mit Konfiguration und Bestell-CSV für Tests."""

    def __init__(self, ordner: Path):
        self.ordner = ordner
        self.csv = ordner / "bestellungen.csv"
        self.config = ordner / "config.toml"
        self.extra = {"rechnung": "", "email": "", "quelle": "", "ebay": ""}
        self.zeilen: list[str] = []

    def bestellungen(self, *zeilen: str) -> "Projekt":
        self.zeilen = list(zeilen)
        self.csv.write_text(KOPF + "\n" + "\n".join(zeilen) + "\n", encoding="utf-8")
        return self

    def einstellung(self, abschnitt: str, text: str) -> "Projekt":
        self.extra[abschnitt] += textwrap.dedent(text) + "\n"
        return self

    def ebay(self, text: str = "") -> "Projekt":
        """Stellt die Quelle auf eBay um."""
        self.extra["ebay"] = textwrap.dedent(
            """
            [ebay]
            client_id = "APP-ID"
            client_secret = "GEHEIM"
            ru_name = "Test-RuName"
            """
        ) + textwrap.dedent(text)
        return self

    def konfig(self):
        quelle_kopf = 'typ = "ebay"' if self.extra["ebay"] else 'typ = "csv"\npfad = "bestellungen.csv"'
        self.config.write_text(
            textwrap.dedent(
                """\
                [firma]
                name = "Test GmbH"
                strasse = "Teststraße 1"
                plz = "12345"
                ort = "Teststadt"
                steuernummer = "12/345/67890"
                iban = "DE00 1234 5678 9012 3456 78"

                [rechnung]
                {rechnung}
                [quelle]
                {quelle_kopf}
                {quelle}
                [email]
                absender = "rechnung@test.example"
                smtp_server = "smtp.test.example"
                {email}
                [speicher]
                zeitzone = "Europe/Berlin"
                {ebay}
                """
            ).format(quelle_kopf=quelle_kopf, **self.extra),
            encoding="utf-8",
        )
        return laden(self.config)


@pytest.fixture
def projekt(tmp_path: Path) -> Projekt:
    return Projekt(tmp_path)


class FalscherVersand:
    """Sammelt die Nachrichten, statt sie zu versenden. Kann Fehlschläge simulieren."""

    probelauf = False

    def __init__(self, fehler_fuer: set[str] | None = None):
        self.nachrichten = []
        self.fehler_fuer = fehler_fuer or set()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def senden(self, nachricht, kennung):
        from tagesrechnung.versand import VersandFehler

        if str(nachricht["To"].addresses[0].addr_spec) in self.fehler_fuer:
            raise VersandFehler("550 Postfach nicht erreichbar")
        self.nachrichten.append(nachricht)


@pytest.fixture
def versand() -> FalscherVersand:
    return FalscherVersand()
