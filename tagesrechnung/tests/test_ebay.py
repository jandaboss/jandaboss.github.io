"""Tests der eBay-Anbindung gegen einen nachgebildeten eBay-Server (Fulfillment API + OAuth)."""

import base64
import json
import os
import stat
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs, quote, unquote, urlsplit
from zoneinfo import ZoneInfo

import pytest

from tagesrechnung import cli, ebay
from tagesrechnung.ablauf import ausfuehren
from tagesrechnung.konfiguration import KonfigurationsFehler, laden
from tagesrechnung.rechnungsbuch import Rechnungsbuch

from .conftest import FalscherVersand

BERLIN = ZoneInfo("Europe/Berlin")
TAG = date(2026, 11, 22)
ABEND = datetime(2026, 11, 22, 23, 55, tzinfo=BERLIN)
CODE = "v^1.1#i^1#f^0#r^1#I^3#p^3#t^Ul41XzA6MkM="
CODE_IN_URL = quote(CODE, safe="")  # so hängt eBay den Code an die Weiterleitungsadresse
RELAIS = "1a2b3c4d5e@members.ebay.com"


def euro(wert):
    return {"value": str(wert), "currency": "EUR"}


def artikel(titel="Teeservice", preis="49.95", menge=1, sku="TS-100", **extra):
    gesamt = Decimal(preis) * menge
    return {"lineItemId": f"L-{titel}", "legacyItemId": "123456789012", "sku": sku, "title": titel,
            "quantity": menge, "lineItemCost": euro(gesamt), **extra}


def bestellung(nr="12-11111-11111", zeit="2026-11-22T08:41:12.000Z", positionen=None, versand="4.90",
               gesamt=None, zahlstatus="PAID", storno="NONE_REQUESTED", email=RELAIS, name="Erika Musterfrau",
               benutzer="erika_m", land="DE", summen=None, versandstatus=None):
    positionen = positionen if positionen is not None else [artikel()]
    if gesamt is None:
        gesamt = sum((Decimal(a["lineItemCost"]["value"]) for a in positionen), Decimal(0)) + Decimal(versand)
    adresse = {"addressLine1": "Beispielweg 5", "city": "Berlin", "postalCode": "10115", "countryCode": land}
    extra = {"orderFulfillmentStatus": versandstatus} if versandstatus else {}
    return {
        **extra,
        "orderId": nr,
        "creationDate": zeit,
        "orderPaymentStatus": zahlstatus,
        "cancelStatus": {"cancelState": storno},
        "buyer": {
            "username": benutzer,
            "buyerRegistrationAddress": {"fullName": name, "email": email, "contactAddress": adresse},
        },
        "fulfillmentStartInstructions": [
            {"shippingStep": {"shipTo": {"fullName": "Lieferung an", "contactAddress": adresse}}}
        ],
        "pricingSummary": {"deliveryCost": euro(versand), "total": euro(gesamt), **(summen or {})},
        "lineItems": positionen,
    }


class FalschesEbay:
    """Bildet die eBay-Endpunkte nach: OAuth-Token, getOrders und getShippingFulfillments."""

    def __init__(self, *bestellungen):
        self.bestellungen = list(bestellungen)
        self.versand: dict[str, list[str]] = {}  # Bestellnummer → shippedDate je Sendung
        self.anfragen: list[tuple] = []
        self.gueltig: set[str] = set()
        self.nr = 0

    def _token(self):
        self.nr += 1
        token = f"zugang-{self.nr}"
        self.gueltig.add(token)
        return token

    def __call__(self, methode, url, kopf, daten=None):
        self.anfragen.append((methode, url, kopf, daten))
        teile = urlsplit(url)
        assert teile.netloc == "api.ebay.com"
        if teile.path == "/identity/v1/oauth2/token":
            assert methode == "POST"
            if kopf["Authorization"] != "Basic " + base64.b64encode(b"APP-ID:GEHEIM").decode():
                return 401, b'{"error":"invalid_client","error_description":"client authentication failed"}'
            felder = parse_qs(daten.decode())
            if felder["grant_type"] == ["authorization_code"] and felder["code"] == [CODE]:
                assert felder["redirect_uri"] == ["Test-RuName"]
                return 200, json.dumps({"access_token": self._token(), "expires_in": 7200,
                                        "refresh_token": "erneuerung-1",
                                        "refresh_token_expires_in": 47304000}).encode()
            if felder["grant_type"] == ["refresh_token"] and felder["refresh_token"] == ["erneuerung-1"]:
                assert felder["scope"] == [ebay.SCOPE]
                return 200, json.dumps({"access_token": self._token(), "expires_in": 7200}).encode()
            return 400, b'{"error":"invalid_grant","error_description":"the provided authorization grant is invalid"}'
        if teile.path == "/sell/fulfillment/v1/order":
            if kopf.get("Authorization", "").removeprefix("Bearer ") not in self.gueltig:
                return 401, b'{"errors":[{"errorId":1001,"message":"Invalid access token"}]}'
            abfrage = parse_qs(teile.query)
            filter_ = abfrage["filter"][0]
            assert filter_.startswith("creationdate:[") and filter_.endswith("]")
            start, ende = filter_[len("creationdate:["):-1].split("..")
            limit, offset = int(abfrage["limit"][0]), int(abfrage["offset"][0])
            assert limit <= 200
            treffer = [b for b in self.bestellungen if start <= b["creationDate"] <= ende]
            return 200, json.dumps({"orders": treffer[offset:offset + limit], "total": len(treffer),
                                    "limit": limit, "offset": offset}).encode()
        teile_pfad = teile.path.split("/")
        if teile.path.startswith("/sell/fulfillment/v1/order/") and teile_pfad[-1] == "shipping_fulfillment":
            assert methode == "GET" and len(teile_pfad) == 7
            if kopf.get("Authorization", "").removeprefix("Bearer ") not in self.gueltig:
                return 401, b'{"errors":[{"errorId":1001,"message":"Invalid access token"}]}'
            sendungen = [{"fulfillmentId": f"F{i}", "shippedDate": d}
                         for i, d in enumerate(self.versand.get(unquote(teile_pfad[5]), []))]
            return 200, json.dumps({"fulfillments": sendungen, "total": len(sendungen)}).encode()
        return 404, b"{}"

    def versandabfragen(self):
        return [unquote(u.path.split("/")[5]) for u in self.abrufe() if u.path.endswith("/shipping_fulfillment")]

    def abrufe(self):
        return [urlsplit(u) for m, u, *_ in self.anfragen if "/sell/fulfillment/" in u]


@pytest.fixture
def server(monkeypatch):
    falsch = FalschesEbay()
    monkeypatch.setattr(ebay, "_http", falsch)
    return falsch


def verbunden(projekt, server, text=""):
    """Konfiguration mit eBay als Quelle und bereits erteilter Freigabe."""
    konfig = projekt.ebay(text).konfig()
    ebay.EbayKonto(konfig.quelle.ebay).verbinden(f"https://example.github.io/?state=x&code={CODE_IN_URL}&expires_in=299")
    return konfig


def auswerten(b, text="", erst_nach_versand=False, versandzeit=None):
    from tagesrechnung.konfiguration import EbayEinstellungen

    einst = EbayEinstellungen(client_id="x", ru_name="y", token_datei=None, erst_nach_versand=erst_nach_versand)
    for zeile in filter(None, text.splitlines()):
        sku, satz = zeile.split("=")
        einst.ust_saetze[sku.strip()] = Decimal(satz)
    return ebay.auswerten(b, einst, BERLIN, Decimal("19"), versandzeit=versandzeit)


# --- Verbinden und Schlüssel ----------------------------------------------------------------------


def test_anmelde_adresse(projekt):
    konfig = projekt.ebay().konfig()
    adresse = ebay.EbayKonto(konfig.quelle.ebay).anmelde_adresse("zufall123")
    teile = urlsplit(adresse)
    assert (teile.scheme, teile.netloc, teile.path) == ("https", "auth.ebay.com", "/oauth2/authorize")
    assert parse_qs(teile.query) == {
        "client_id": ["APP-ID"], "response_type": ["code"], "redirect_uri": ["Test-RuName"],
        "scope": [ebay.SCOPE], "state": ["zufall123"],
    }


def test_verbinden_speichert_freigabe_geschuetzt(projekt, server):
    konfig = projekt.ebay().konfig()
    konto = ebay.EbayKonto(konfig.quelle.ebay)
    # eBay hängt den Code URL-kodiert an die Weiterleitungsadresse an
    adresse = ("https://example.github.io/?state=abc&code=v%5E1.1%23i%5E1%23f%5E0%23r%5E1%23I%5E3%23p%5E3"
               "%23t%5EUl41XzA6MkM%3D&expires_in=299")
    bis = konto.verbinden(adresse, state="abc")

    assert 540 < (bis - datetime.now(timezone.utc)).days < 550  # ca. 18 Monate
    datei = konfig.quelle.ebay.token_datei
    assert datei.name == "ebay-zugang.json" and datei.parent == projekt.ordner
    if os.name == "posix":
        assert stat.S_IMODE(datei.stat().st_mode) == 0o600
    gespeichert = json.loads(datei.read_text())
    assert gespeichert["refresh_token"] == "erneuerung-1"
    assert gespeichert["umgebung"] == "produktion"


@pytest.mark.parametrize(
    "eingabe, meldung",
    [
        ("https://example.github.io/?state=fremd&code=abc", "nicht aus dieser Anmeldung"),
        ("https://example.github.io/?state=abc&error=access_denied", "abgelehnt"),
        ("https://example.github.io/?state=abc", "kein Code"),
        ("https://example.github.io/?state=abc&code=veraltet", "invalid_grant"),
    ],
)
def test_verbinden_fehler(projekt, server, eingabe, meldung):
    konfig = projekt.ebay().konfig()
    with pytest.raises(ebay.EbayFehler, match=meldung):
        ebay.EbayKonto(konfig.quelle.ebay).verbinden(eingabe, state="abc")
    assert not konfig.quelle.ebay.token_datei.exists()


def test_secret_aus_umgebungsvariable(projekt, server, monkeypatch):
    konfig = projekt.ebay().konfig()
    konfig.quelle.ebay.client_secret = ""
    with pytest.raises(ebay.EbayFehler, match="TAGESRECHNUNG_EBAY_SECRET"):
        ebay.EbayKonto(konfig.quelle.ebay).verbinden(CODE)
    monkeypatch.setenv("TAGESRECHNUNG_EBAY_SECRET", "GEHEIM")
    ebay.EbayKonto(konfig.quelle.ebay).verbinden(CODE)  # nur der Code geht auch


def test_zugangsschluessel_wird_erneuert_und_wiederverwendet(projekt, server):
    konfig = verbunden(projekt, server)
    uhr = [time.time()]
    konto = ebay.EbayKonto(konfig.quelle.ebay, uhr=lambda: uhr[0])

    assert konto.zugangsschluessel() == "zugang-1"  # noch gültig → keine Anfrage
    anfragen = len(server.anfragen)
    uhr[0] += 7200  # zwei Stunden später
    assert konto.zugangsschluessel() == "zugang-2"
    assert len(server.anfragen) == anfragen + 1
    assert konto.zugangsschluessel() == "zugang-2"


def test_abgelaufene_freigabe(projekt, server):
    konfig = verbunden(projekt, server)
    konto = ebay.EbayKonto(konfig.quelle.ebay, uhr=lambda: time.time() + 47304000 + 1)
    with pytest.raises(ebay.EbayFehler, match="abgelaufen.*ebay-verbinden"):
        konto.zugangsschluessel()


def test_ohne_verbindung(projekt, server):
    konfig = projekt.ebay().konfig()
    with pytest.raises(ebay.EbayFehler, match="Noch nicht mit eBay verbunden"):
        ebay.EbayKonto(konfig.quelle.ebay).zugangsschluessel()


# --- Bestellungen abrufen -------------------------------------------------------------------------


def test_abruf_ganzer_tag_in_deutscher_zeit_mit_mehreren_seiten(projekt, server):
    konfig = verbunden(projekt, server)
    server.bestellungen = [bestellung(nr=f"12-{i:05d}-00000", zeit=f"2026-11-22T{i % 23:02d}:00:00.000Z")
                           for i in range(250)]
    server.bestellungen.append(bestellung(nr="spaet", zeit="2026-11-22T23:30:00.000Z"))  # 00:30 am 23.11.
    konto = ebay.EbayKonto(konfig.quelle.ebay)
    ergebnis = ebay.abrufen(konfig.quelle.ebay, TAG, TAG, BERLIN, Decimal(19), konto=konto)

    filter_ = parse_qs(server.abrufe()[0].query)["filter"][0]
    assert filter_ == "creationdate:[2026-11-21T23:00:00.000Z..2026-11-22T22:59:59.999Z]"
    assert [parse_qs(a.query)["offset"] for a in server.abrufe()] == [["0"], ["200"]]
    assert len(ergebnis) == 250 and "spaet" not in {b.nummer for b in ergebnis}


def test_abgelehnter_schluessel_wird_einmal_erneuert(projekt, server):
    konfig = verbunden(projekt, server)
    server.bestellungen = [bestellung()]
    server.gueltig.clear()  # eBay hat den Zugangsschlüssel widerrufen
    ergebnis = ebay.abrufen(konfig.quelle.ebay, TAG, TAG, BERLIN, Decimal(19))
    assert len(ergebnis) == 1
    assert len(server.abrufe()) == 2


def test_zugriff_verweigert(projekt, server, monkeypatch):
    konfig = verbunden(projekt, server)
    monkeypatch.setattr(ebay, "_http", lambda *a: (403, b'{"errors":[{"message":"Insufficient permissions"}]}'))
    with pytest.raises(ebay.EbayFehler, match="Insufficient permissions.*freigeschaltet"):
        ebay.abrufen(konfig.quelle.ebay, TAG, TAG, BERLIN, Decimal(19))


# --- Bestellungen auswerten -----------------------------------------------------------------------


def test_bestellung_wird_zu_positionen():
    b = auswerten(bestellung(positionen=[artikel("Teeservice", "49.95"), artikel("Tassen", "6.50", menge=4)]))
    assert b.abrechenbar and not b.fehler
    assert [(p.artikel, p.menge, p.einzelpreis, p.artikelnummer) for p in b.positionen] == [
        ("Teeservice", 1, Decimal("49.95"), "TS-100"),
        ("Tassen", 4, Decimal("6.50"), "TS-100"),
        ("Versandkosten", 1, Decimal("4.90"), ""),
    ]
    p = b.positionen[0]
    assert p.bestellnummer == "12-11111-11111"
    assert p.bestellzeit == datetime(2026, 11, 22, 9, 41, 12)  # 08:41 UTC = 09:41 in Berlin
    assert (p.kunde.name, p.kunde.email, p.kunde.schluessel) == ("Erika Musterfrau", RELAIS, "ebay:erika_m")
    assert p.kunde.anschrift() == ["Erika Musterfrau", "Beispielweg 5", "10115 Berlin", "Deutschland"]
    assert b.betrag == Decimal("80.85")


def test_preis_nicht_auf_cent_teilbar():
    b = auswerten(bestellung(positionen=[{"title": "Knöpfe", "quantity": 3, "lineItemCost": euro("10.00")}],
                             versand="0.00"))
    assert [(p.artikel, p.menge, p.einzelpreis) for p in b.positionen] == [("3 × Knöpfe", 1, Decimal("10.00"))]


def test_rabatt_auf_artikel():
    pos = artikel("Teeservice", "50.00", discountedLineItemCost=euro("45.00"))
    b = auswerten(bestellung(positionen=[pos], versand="0", gesamt="45.00", summen={"priceDiscount": euro("5.00")}))
    assert [(p.artikel, p.gesamt) for p in b.positionen] == [("Teeservice", Decimal("50.00")),
                                                            ("Rabatt", Decimal("-5.00"))]


@pytest.mark.parametrize("vorzeichen", ["", "-"])
def test_rabatte_nur_in_der_summe(vorzeichen):
    b = auswerten(bestellung(
        positionen=[artikel("Teeservice", "50.00")], versand="4.90", gesamt="45.00",
        summen={"priceDiscount": euro(f"{vorzeichen}5.00"), "deliveryDiscount": euro(f"{vorzeichen}4.90")},
    ))
    assert not b.fehler, b.grund
    assert [(p.artikel, p.gesamt) for p in b.positionen] == [
        ("Teeservice", Decimal("50.00")), ("Versandkosten", Decimal("4.90")),
        ("Rabatt", Decimal("-5.00")), ("Versandrabatt", Decimal("-4.90")),
    ]


def test_summe_passt_nicht_wird_nicht_abgerechnet():
    b = auswerten(bestellung(gesamt="99.99"))
    assert b.fehler and not b.abrechenbar
    assert "passt nicht zum eBay-Gesamtbetrag 99,99 €" in b.grund


def test_von_ebay_erhobene_steuer_gehoert_nicht_auf_die_rechnung():
    b = auswerten(bestellung(land="GB", gesamt="65.85", summen={"tax": euro("11.00")}))
    assert not b.fehler, b.grund
    assert sum(p.gesamt for p in b.positionen) == Decimal("54.85")
    assert b.positionen[0].kunde.land == "Vereinigtes Königreich"


@pytest.mark.parametrize(
    "zahlstatus, storno, grund, fehler",
    [
        ("PENDING", "NONE_REQUESTED", "noch nicht bezahlt", False),
        ("FAILED", "NONE_REQUESTED", "noch nicht bezahlt", False),
        ("PAID", "CANCELED", "storniert", False),
        ("PAID", "IN_PROGRESS", "Stornierung beantragt", False),
        ("FULLY_REFUNDED", "NONE_REQUESTED", "vollständig erstattet", False),
        ("PARTIALLY_REFUNDED", "NONE_REQUESTED", "teilweise erstattet", True),
    ],
)
def test_nur_bezahlte_und_nicht_stornierte(zahlstatus, storno, grund, fehler):
    b = auswerten(bestellung(zahlstatus=zahlstatus, storno=storno))
    assert not b.abrechenbar
    assert grund in b.grund and b.fehler is fehler


def test_ohne_email_keine_rechnung():
    b = auswerten(bestellung(email=None))
    assert b.fehler and "E-Mail" in b.grund and not b.hat_email


def test_fremdwaehrung_wird_abgelehnt():
    b = auswerten(bestellung(positionen=[{"title": "X", "quantity": 1,
                                         "lineItemCost": {"value": "10", "currency": "GBP"}}]))
    assert b.fehler and "GBP" in b.grund


def test_ermaessigter_steuersatz_und_anteilige_versandkosten():
    b = auswerten(
        bestellung(
            positionen=[artikel("Teeservice", "60.00", sku="TS-100"), artikel("Kochbuch", "20.00", sku="BUCH-1")],
            versand="6.00",
        ),
        "BUCH-1 = 7",
    )
    assert [(p.artikel, p.gesamt, p.ust_satz) for p in b.positionen] == [
        ("Teeservice", Decimal("60.00"), 19),
        ("Kochbuch", Decimal("20.00"), 7),
        ("Versandkosten (anteilig, 19 % USt.)", Decimal("4.50"), 19),
        ("Versandkosten (anteilig, 7 % USt.)", Decimal("1.50"), 7),
    ]


# --- Zusammenspiel mit dem Tageslauf ----------------------------------------------------------------


def test_tageslauf_mit_ebay(projekt, server):
    konfig = verbunden(projekt, server)
    server.bestellungen = [
        bestellung("12-00001-00001", "2026-11-22T08:00:00.000Z"),
        bestellung("12-00002-00002", "2026-11-22T15:00:00.000Z", positionen=[artikel("Tassen", "6.50", 2)]),
        bestellung("12-00003-00003", "2026-11-22T10:00:00.000Z", zahlstatus="PENDING", benutzer="max",
                   email="9z8y7x@members.ebay.com", name="Max Mustermann"),
    ]
    versand = FalscherVersand()
    bericht = ausfuehren(konfig, TAG, versand=versand, jetzt=ABEND)

    assert bericht.erfolgreich, bericht.datenfehler
    assert bericht.erstellt == ["RE-2026-00001"]  # Sammelrechnung für Erikas zwei Bestellungen
    (nachricht,) = versand.nachrichten
    assert nachricht["To"].addresses[0].addr_spec == RELAIS
    assert "12-00001-00001, 12-00002-00002" in nachricht.get_body(("plain",)).get_content()

    # Max bezahlt seine Auktion zwei Tage später → wird mit --nachholen nachträglich abgerechnet
    server.bestellungen[2]["orderPaymentStatus"] = "PAID"
    bericht = ausfuehren(konfig, date(2026, 11, 24), nachholen_tage=7, versand=versand,
                         jetzt=datetime(2026, 11, 24, 23, 55, tzinfo=BERLIN))
    assert bericht.erstellt == ["RE-2026-00002"]
    assert bericht.bereits_abgerechnet == 2
    buch = Rechnungsbuch.oeffnen(konfig.speicher.datenbank)
    try:
        max_ = buch.holen("RE-2026-00002")
    finally:
        buch.schliessen()
    assert (max_.kunde_name, max_.leistungsdatum, max_.rechnungsdatum) == ("Max Mustermann", TAG, date(2026, 11, 24))


def test_tageslauf_meldet_ebay_probleme(projekt, server):
    konfig = projekt.ebay().konfig()  # nicht verbunden
    bericht = ausfuehren(konfig, TAG, versand=FalscherVersand(), jetzt=ABEND)
    assert not bericht.erfolgreich
    assert "Noch nicht mit eBay verbunden" in bericht.datenfehler[0]


def test_abrechnen_ab_schuetzt_alte_bestellungen(projekt, server):
    konfig = verbunden(projekt, server, "")
    projekt.einstellung("rechnung", "abrechnen_ab = 2026-11-22")
    konfig = projekt.konfig()
    server.bestellungen = [bestellung("alt", "2026-11-20T10:00:00.000Z"), bestellung("neu", "2026-11-22T10:00:00.000Z",
                                                                                     benutzer="max")]
    bericht = ausfuehren(konfig, TAG, nachholen_tage=7, versand=FalscherVersand(), jetzt=ABEND)
    assert len(bericht.erstellt) == 1
    filter_ = parse_qs(server.abrufe()[-1].query)["filter"][0]
    assert filter_.startswith("creationdate:[2026-11-21T23:00:00.000Z")


def test_ebay_beispielkonfiguration_ist_gueltig():
    from pathlib import Path

    konfig = laden(Path(__file__).parent.parent / "config.ebay.beispiel.toml")
    assert konfig.quelle.typ == "ebay" and konfig.quelle.ebay.umgebung == "produktion"
    assert konfig.quelle.ebay.token_datei.name == "ebay-zugang.json"
    assert konfig.rechnung.zahlungsziel_tage == 0 and "über eBay abgewickelt" in konfig.rechnung.zahlungshinweis
    assert konfig.rechnung.abrechnen_ab == date(2026, 11, 24)
    assert konfig.rechnung.kleinunternehmer


def test_ebay_ohne_abschnitt(projekt):
    projekt.konfig()
    projekt.config.write_text(projekt.config.read_text().replace("typ = \"csv\"", "typ = \"ebay\""))
    with pytest.raises(KonfigurationsFehler, match=r"\[ebay\] fehlt"):
        laden(projekt.config)


# --- Kommandozeile ------------------------------------------------------------------------------------


def test_cli_verbinden_und_pruefen(projekt, server, capsys):
    projekt.ebay().konfig()
    c = ["-c", str(projekt.config)]
    assert cli.main(["ebay-verbinden", *c, "--adresse", f"https://example.github.io/?code={CODE_IN_URL}"]) == 0
    server.bestellungen = [
        bestellung("12-00001-00001", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")),
        bestellung("12-00002-00002", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                   zahlstatus="PENDING", benutzer="max"),
    ]
    capsys.readouterr()
    assert cli.main(["ebay-pruefen", *c]) == 0
    ausgabe = capsys.readouterr().out
    assert "12-00001-00001" in ausgabe and "wird abgerechnet" in ausgabe
    assert "noch nicht bezahlt" in ausgabe
    assert "eBay-Freigabe gültig bis" in ausgabe


def test_cli_verbinden_fehler(projekt, server):
    projekt.ebay().konfig()
    assert cli.main(["ebay-verbinden", "-c", str(projekt.config), "--adresse", "https://x/?code=falsch"]) == 1
    projekt.extra["ebay"] = ""
    projekt.konfig()
    assert cli.main(["ebay-verbinden", "-c", str(projekt.config), "--adresse", "x"]) == 2


# --- Erst nach dem Versand abrechnen (erst_nach_versand) ---------------------------------------------


@pytest.mark.parametrize("versandstatus, grund", [
    (None, "noch nicht versendet"),
    ("NOT_STARTED", "noch nicht versendet"),
    ("IN_PROGRESS", "teilweise versendet"),
])
def test_erst_nach_versand_wartet_auf_den_versand(versandstatus, grund):
    b = auswerten(bestellung(versandstatus=versandstatus), erst_nach_versand=True)
    assert not b.abrechenbar and not b.fehler
    assert b.grund.startswith(grund)


def test_ohne_schalter_wird_der_versand_nicht_abgewartet():
    b = auswerten(bestellung(versandstatus="NOT_STARTED"))
    assert b.abrechenbar
    assert all(p.lieferdatum is None for p in b.positionen)


def test_fehler_werden_schon_vor_dem_versand_gemeldet():
    b = auswerten(bestellung(versandstatus="NOT_STARTED", email=""), erst_nach_versand=True)
    assert b.fehler and "E-Mail" in b.grund


def test_versandtag_wird_liefer_und_leistungsdatum():
    versendet = datetime(2026, 11, 24, 0, 14)  # Ortszeit, wie EbayKonto.versandzeit sie liefert
    b = auswerten(bestellung(versandstatus="FULFILLED"), erst_nach_versand=True, versandzeit=versendet)
    assert b.abrechenbar and b.versandzeit == versendet
    assert {p.lieferdatum for p in b.positionen} == {date(2026, 11, 24)}


def test_versendet_ohne_versandtag_nimmt_das_bestelldatum():
    b = auswerten(bestellung(versandstatus="FULFILLED"), erst_nach_versand=True)
    assert b.abrechenbar
    assert all(p.lieferdatum is None for p in b.positionen)


def test_versandzeit_nimmt_die_letzte_sendung_in_ortszeit(projekt, server):
    konfig = verbunden(projekt, server)
    server.versand["12-00001/00001"] = ["2026-11-22T09:00:00.000Z", "2026-11-23T23:30:00.000Z"]
    konto = ebay.EbayKonto(konfig.quelle.ebay)
    assert konto.versandzeit("12-00001/00001", BERLIN) == datetime(2026, 11, 24, 0, 30)  # 23:30 UTC = 0:30 MEZ
    assert konto.versandzeit("12-99999-99999", BERLIN) is None


def test_tageslauf_rechnet_erst_nach_dem_versand_ab(projekt, server):
    konfig = verbunden(projekt, server, "erst_nach_versand = true\n")
    server.bestellungen = [
        bestellung("12-00001-00001", "2026-11-21T09:00:00.000Z", versandstatus="FULFILLED"),
        bestellung("12-00002-00002", "2026-11-22T10:00:00.000Z", versandstatus="NOT_STARTED", benutzer="max",
                   email="9z8y7x@members.ebay.com", name="Max Mustermann"),
    ]
    server.versand["12-00001-00001"] = ["2026-11-22T14:00:00.000Z"]  # bestellt am 21., versendet am 22.

    bericht = ausfuehren(konfig, TAG, nachholen_tage=7, versand=FalscherVersand(), jetzt=ABEND)
    assert bericht.erfolgreich, bericht.datenfehler
    assert bericht.erstellt == ["RE-2026-00001"]
    assert server.versandabfragen() == ["12-00001-00001"]  # nur für versendete Bestellungen

    # Max' Bestellung wird zwei Tage später versendet (kurz nach Mitternacht deutscher Zeit eingetragen)
    server.bestellungen[1]["orderFulfillmentStatus"] = "FULFILLED"
    server.versand["12-00002-00002"] = ["2026-11-23T23:30:00.000Z"]
    bericht = ausfuehren(konfig, date(2026, 11, 24), nachholen_tage=7, versand=FalscherVersand(),
                         jetzt=datetime(2026, 11, 24, 17, 0, tzinfo=BERLIN))
    assert bericht.erstellt == ["RE-2026-00002"] and bericht.bereits_abgerechnet == 1

    buch = Rechnungsbuch.oeffnen(konfig.speicher.datenbank)
    try:
        erika, max_ = buch.holen("RE-2026-00001"), buch.holen("RE-2026-00002")
    finally:
        buch.schliessen()
    assert (erika.leistungsdatum, erika.rechnungsdatum) == (date(2026, 11, 22), TAG)
    assert (max_.leistungsdatum, max_.rechnungsdatum) == (date(2026, 11, 24), date(2026, 11, 24))


def test_cli_pruefen_zeigt_versandtag(projekt, server, capsys):
    projekt.ebay("erst_nach_versand = true\n").konfig()
    c = ["-c", str(projekt.config)]
    assert cli.main(["ebay-verbinden", *c, "--adresse", f"https://example.github.io/?code={CODE_IN_URL}"]) == 0
    jetzt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    server.bestellungen = [
        bestellung("12-00001-00001", jetzt, versandstatus="FULFILLED"),
        bestellung("12-00002-00002", jetzt, versandstatus="NOT_STARTED", benutzer="max"),
    ]
    server.versand["12-00001-00001"] = [jetzt]
    capsys.readouterr()
    assert cli.main(["ebay-pruefen", *c]) == 0
    ausgabe = capsys.readouterr().out
    heute = datetime.now(BERLIN).strftime("%d.%m.%Y")
    assert f"wird abgerechnet (versendet {heute})" in ausgabe
    assert "noch nicht versendet" in ausgabe


def test_beispielkonfiguration_erst_nach_versand(projekt):
    assert projekt.ebay().konfig().quelle.ebay.erst_nach_versand is False
    assert projekt.ebay("erst_nach_versand = true\n").konfig().quelle.ebay.erst_nach_versand is True
    with pytest.raises(KonfigurationsFehler, match="erst_nach_versand"):
        projekt.ebay('erst_nach_versand = "ja"\n').konfig()
