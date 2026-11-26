"""eBay-Anbindung: holt Bestellungen über die eBay Sell Fulfillment API.

Einmalig wird das Programm mit dem eBay-Konto verbunden (``tagesrechnung ebay-verbinden``):
Sie melden sich im Browser bei eBay an und erlauben dem Programm, Ihre Bestellungen zu
**lesen**. Die Freigabe (Refresh-Token) gilt laut eBay etwa 18 Monate und wird in der
``token_datei`` gespeichert. Danach holt das Programm bei jedem Lauf selbst einen kurzlebigen
Zugangsschlüssel und ruft die Bestellungen ab.

Abgerechnet werden nur Bestellungen, die bezahlt und nicht storniert sind. Bevor eine Bestellung
abgerechnet wird, muss die Summe der Rechnungspositionen genau dem Gesamtbetrag entsprechen,
den eBay meldet – sonst wird sie als Fehler gemeldet statt mit falschem Betrag abgerechnet.
"""

from __future__ import annotations

import base64
import itertools
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from datetime import time as uhrzeit
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from . import formate
from .formate import auf_cent, zeitpunkt_lesen
from .konfiguration import EbayEinstellungen
from .modelle import Kunde, Position
from .netz import ssl_kontext

log = logging.getLogger(__name__)

SCOPE = "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly"
SERVER = {
    "produktion": ("https://api.ebay.com", "https://auth.ebay.com"),
    "sandbox": ("https://api.sandbox.ebay.com", "https://auth.sandbox.ebay.com"),
}
SEITENGROESSE = 200  # Höchstwert von getOrders
WARNEN_TAGE_VOR_ABLAUF = 30

LAENDER = {
    "DE": "Deutschland", "AT": "Österreich", "CH": "Schweiz", "LI": "Liechtenstein", "LU": "Luxemburg",
    "NL": "Niederlande", "BE": "Belgien", "FR": "Frankreich", "IT": "Italien", "ES": "Spanien",
    "PT": "Portugal", "PL": "Polen", "CZ": "Tschechien", "SK": "Slowakei", "HU": "Ungarn", "DK": "Dänemark",
    "SE": "Schweden", "FI": "Finnland", "IE": "Irland", "GR": "Griechenland", "HR": "Kroatien",
    "SI": "Slowenien", "RO": "Rumänien", "BG": "Bulgarien", "GB": "Vereinigtes Königreich",
    "US": "Vereinigte Staaten",
}


class EbayFehler(Exception):
    pass


# Käuferhinweise, die wahrscheinlich die Rechnung betreffen („Rechnung bitte auf Fa. … GmbH“)
_RECHNUNGSHINWEIS = re.compile(
    r"rechnung|firma|\bfa\b|gmbh|\bag\b|\bug\b|\bkg\b|\bohg\b|\bgbr\b|\be\.\s?[kv]\.|\bust\b|ust-?id|"
    r"steuer|ausstellen|invoice|billing|company|\bvat\b",
    re.IGNORECASE,
)


def betrifft_rechnung(hinweis: str) -> bool:
    return bool(_RECHNUNGSHINWEIS.search(hinweis))


# --- HTTP ---------------------------------------------------------------------------------------


def _http(methode: str, url: str, kopf: dict[str, str], daten: bytes | None = None) -> tuple[int, bytes]:
    anfrage = urllib.request.Request(url, data=daten, headers=kopf, method=methode)
    try:
        with urllib.request.urlopen(anfrage, timeout=60, context=ssl_kontext()) as antwort:
            return antwort.status, antwort.read()
    except urllib.error.HTTPError as fehler:
        return fehler.code, fehler.read()
    except (urllib.error.URLError, TimeoutError, OSError) as fehler:
        raise EbayFehler(f"eBay ist nicht erreichbar ({getattr(fehler, 'reason', fehler)})") from None


def _fehlertext(status: int, inhalt: bytes) -> str:
    try:
        daten = json.loads(inhalt)
    except ValueError:
        return f"HTTP {status}"
    if isinstance(daten, dict):
        if daten.get("errors"):
            e = daten["errors"][0]
            return f"HTTP {status}: {e.get('longMessage') or e.get('message') or e}"
        if "error" in daten:  # OAuth-Fehler
            return f"HTTP {status}: {daten['error']} – {daten.get('error_description', '')}".rstrip(" –")
    return f"HTTP {status}"


def _ebay_zeit(zeit: datetime) -> str:
    """eBay erwartet UTC mit Millisekunden: 2026-11-22T23:00:00.000Z"""
    utc = zeit.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


# --- Konto und Freigabe -------------------------------------------------------------------------


class EbayKonto:
    def __init__(self, einst: EbayEinstellungen, http=None, uhr=None):
        self.e = einst
        self._http = http or _http
        self._uhr = uhr or time.time
        self._api, self._auth = SERVER[einst.umgebung]

    def anmelde_adresse(self, state: str) -> str:
        parameter = {
            "client_id": self.e.client_id,
            "response_type": "code",
            "redirect_uri": self.e.ru_name,
            "scope": SCOPE,
            "state": state,
        }
        return f"{self._auth}/oauth2/authorize?" + urllib.parse.urlencode(parameter, quote_via=urllib.parse.quote)

    def verbinden(self, eingabe: str, state: str | None = None) -> datetime:
        """Löst den Code aus der Weiterleitungsadresse ein und speichert die Freigabe.
        Gibt zurück, bis wann die Freigabe gilt."""
        code = _code_aus_eingabe(eingabe, state)
        daten = self._token_anfrage(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": self.e.ru_name}
        )
        if "refresh_token" not in daten or "access_token" not in daten:
            raise EbayFehler("eBay hat keine Freigabe zurückgegeben – bitte erneut versuchen")
        jetzt = self._uhr()
        zugang = {
            "umgebung": self.e.umgebung,
            "refresh_token": daten["refresh_token"],
            "refresh_token_ablauf": jetzt + int(daten.get("refresh_token_expires_in", 47_304_000)),
            "access_token": daten["access_token"],
            "access_token_ablauf": jetzt + int(daten.get("expires_in", 7200)),
        }
        self._speichern(zugang)
        return datetime.fromtimestamp(zugang["refresh_token_ablauf"], timezone.utc)

    def freigabe_bis(self) -> datetime | None:
        try:
            zugang = self._laden()
        except EbayFehler:
            return None
        return datetime.fromtimestamp(zugang["refresh_token_ablauf"], timezone.utc)

    def zugangsschluessel(self, erneuern: bool = False) -> str:
        zugang = self._laden()
        jetzt = self._uhr()
        if not erneuern and zugang.get("access_token") and zugang.get("access_token_ablauf", 0) > jetzt + 120:
            return zugang["access_token"]
        if zugang["refresh_token_ablauf"] <= jetzt:
            raise EbayFehler("Die eBay-Freigabe ist abgelaufen. Bitte neu verbinden: tagesrechnung ebay-verbinden")
        try:
            daten = self._token_anfrage(
                {"grant_type": "refresh_token", "refresh_token": zugang["refresh_token"], "scope": SCOPE}
            )
        except EbayFehler as fehler:
            raise EbayFehler(f"{fehler}. Falls die Freigabe widerrufen wurde: tagesrechnung ebay-verbinden") from None
        zugang["access_token"] = daten["access_token"]
        zugang["access_token_ablauf"] = jetzt + int(daten.get("expires_in", 7200))
        self._speichern(zugang)
        return zugang["access_token"]

    def _token_anfrage(self, felder: dict[str, str]) -> dict:
        secret = self.e.secret()
        if not secret:
            raise EbayFehler(
                f"eBay-Schlüssel (Cert ID) fehlt: Umgebungsvariable {self.e.client_secret_env} setzen "
                "oder [ebay] client_secret angeben"
            )
        anmeldung = base64.b64encode(f"{self.e.client_id}:{secret}".encode()).decode()
        status, inhalt = self._http(
            "POST",
            f"{self._api}/identity/v1/oauth2/token",
            {
                "Authorization": f"Basic {anmeldung}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            urllib.parse.urlencode(felder).encode(),
        )
        if status != 200:
            raise EbayFehler(f"Anmeldung bei eBay fehlgeschlagen ({_fehlertext(status, inhalt)})")
        return json.loads(inhalt)

    def _laden(self) -> dict:
        pfad = self.e.token_datei
        try:
            zugang = json.loads(pfad.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise EbayFehler("Noch nicht mit eBay verbunden. Einmalig ausführen: tagesrechnung ebay-verbinden") from None
        except (ValueError, OSError):
            raise EbayFehler(f"{pfad} ist nicht lesbar. Bitte neu verbinden: tagesrechnung ebay-verbinden") from None
        if zugang.get("umgebung") != self.e.umgebung:
            raise EbayFehler(
                f"Die gespeicherte Freigabe gehört zur eBay-Umgebung {zugang.get('umgebung')!r}, eingestellt ist "
                f"{self.e.umgebung!r}. Bitte neu verbinden: tagesrechnung ebay-verbinden"
            )
        return zugang

    def _speichern(self, zugang: dict) -> None:
        """Schreibt die Freigabe atomar und nur für den eigenen Benutzer lesbar (enthält den Refresh-Token)."""
        pfad = self.e.token_datei
        pfad.parent.mkdir(parents=True, exist_ok=True)
        temp = pfad.with_name(pfad.name + ".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(zugang, f, indent=2)
        os.replace(temp, pfad)

    # --- Bestellungen ----------------------------------------------------------------------------

    def bestellungen(self, von: datetime, bis: datetime) -> list[dict]:
        """Alle Bestellungen, die zwischen ``von`` und ``bis`` (jeweils einschließlich) angelegt wurden."""
        filter_ = f"creationdate:[{_ebay_zeit(von)}..{_ebay_zeit(bis)}]"
        alle: list[dict] = []
        while True:
            abfrage = urllib.parse.urlencode(
                {"filter": filter_, "limit": SEITENGROESSE, "offset": len(alle)}, quote_via=urllib.parse.quote
            )
            daten = self._abrufen(f"{self._api}/sell/fulfillment/v1/order?{abfrage}")
            seite = daten.get("orders") or []
            alle.extend(seite)
            if not seite or len(alle) >= int(daten.get("total", 0)):
                return alle

    def _abrufen(self, url: str) -> dict:
        for versuch in (1, 2):
            schluessel = self.zugangsschluessel(erneuern=versuch == 2)
            status, inhalt = self._http(
                "GET", url, {"Authorization": f"Bearer {schluessel}", "Accept": "application/json"}
            )
            if status == 401 and versuch == 1:
                continue  # Schlüssel abgelaufen oder widerrufen → einmal erneuern
            if status != 200:
                hinweis = ""
                if status in (401, 403):
                    hinweis = (" – ist der eBay-Schlüssel für den Echtbetrieb freigeschaltet und die Freigabe "
                               "erteilt (tagesrechnung ebay-verbinden)?")
                raise EbayFehler(f"eBay-Bestellungen nicht abrufbar ({_fehlertext(status, inhalt)}){hinweis}")
            return json.loads(inhalt)
        raise AssertionError("unerreichbar")


def _code_aus_eingabe(eingabe: str, state: str | None) -> str:
    """Akzeptiert die komplette Adresse, auf der eBay nach der Anmeldung gelandet ist, oder nur den Code."""
    text = eingabe.strip()
    if not text:
        raise EbayFehler("Keine Adresse eingegeben")
    if "://" in text or text.startswith("?") or any(f"{n}=" in text for n in ("code", "state", "error")):
        abfrage = urllib.parse.urlsplit(text).query if "://" in text else text.lstrip("?")
        werte = urllib.parse.parse_qs(abfrage)
        if "error" in werte or werte.get("isAuthSuccessful") == ["false"]:
            raise EbayFehler("eBay meldet: Freigabe abgelehnt oder fehlgeschlagen. Bitte erneut versuchen.")
        if state and werte.get("state", [state])[0] != state:
            raise EbayFehler("Die Adresse stammt nicht aus dieser Anmeldung. Bitte den neuen Link benutzen.")
        if "code" not in werte:
            raise EbayFehler("In der Adresse steht kein Code (…?code=…). Bitte die komplette Adresse kopieren.")
        return werte["code"][0]
    return urllib.parse.unquote(text)


# --- Bestellungen auswerten -----------------------------------------------------------------------


@dataclass
class EbayBestellung:
    nummer: str
    zeit: datetime
    kaeufer: str
    betrag: Decimal | None
    zahlstatus: str
    hat_email: bool
    positionen: list[Position] = field(default_factory=list)
    grund: str = ""  # warum (noch) nicht abrechenbar
    fehler: bool = False  # True: muss von Hand geklärt werden
    hinweis: str = ""  # Nachricht des Käufers beim Kauf

    @property
    def abrechenbar(self) -> bool:
        return bool(self.positionen)


def _betrag(daten: dict | None, feld: str) -> Decimal | None:
    wert = (daten or {}).get(feld)
    if not wert or wert.get("value") in (None, ""):
        return None
    if wert.get("currency", "EUR") != "EUR":
        raise ValueError(f"Währung {wert.get('currency')} wird nicht unterstützt (nur EUR)")
    try:
        return Decimal(str(wert["value"]))
    except InvalidOperation:
        raise ValueError(f"ungültiger Betrag {wert['value']!r} in {feld}") from None


def auswerten(
    bestellung: dict, einst: EbayEinstellungen, zeitzone: ZoneInfo, standard_ust_satz: Decimal,
    freigegeben: frozenset[str] | set[str] = frozenset(),
) -> EbayBestellung:
    """``freigegeben``: Bestellnummern, für die eine Rechnungsanschrift eingetragen oder die
    ausdrücklich freigegeben wurden – Käuferhinweise halten sie nicht mehr an."""
    nummer = str(bestellung.get("orderId") or "?")
    kaeufer_daten = bestellung.get("buyer") or {}
    registriert = kaeufer_daten.get("buyerRegistrationAddress") or {}
    lieferung = _lieferadresse(bestellung)
    zahlstatus = str(bestellung.get("orderPaymentStatus") or "")
    storno = str((bestellung.get("cancelStatus") or {}).get("cancelState") or "")

    ergebnis = EbayBestellung(
        nummer=nummer,
        zeit=datetime.min,
        kaeufer=registriert.get("fullName") or lieferung.get("fullName") or kaeufer_daten.get("username") or "?",
        betrag=None,
        zahlstatus=zahlstatus,
        hat_email=bool(registriert.get("email") or lieferung.get("email")),
        hinweis=str(bestellung.get("buyerCheckoutNotes") or "").strip(),
    )
    try:
        ergebnis.zeit = zeitpunkt_lesen(bestellung.get("creationDate"), zeitzone)
        ergebnis.betrag = _betrag(bestellung.get("pricingSummary"), "total")
    except ValueError as e:
        ergebnis.grund, ergebnis.fehler = str(e), True
        return ergebnis

    if storno == "CANCELED":
        ergebnis.grund = "storniert"
    elif storno == "IN_PROGRESS":
        ergebnis.grund = "Stornierung beantragt – wird abgerechnet, falls sie abgelehnt wird"
    elif zahlstatus == "FULLY_REFUNDED":
        ergebnis.grund = "vollständig erstattet"
    elif zahlstatus == "PARTIALLY_REFUNDED":
        ergebnis.grund, ergebnis.fehler = "teilweise erstattet – bitte von Hand abrechnen", True
    elif einst.nur_bezahlte and zahlstatus != "PAID":
        ergebnis.grund = "noch nicht bezahlt"
    if ergebnis.grund:
        return ergebnis

    if ergebnis.hinweis and nummer not in freigegeben and betrifft_rechnung(ergebnis.hinweis):
        ergebnis.grund = (
            f"Käuferhinweis zur Rechnung: „{ergebnis.hinweis}“ – Rechnungsanschrift eintragen mit "
            f"„tagesrechnung anschrift {nummer} --firma … --strasse … --plz … --ort …“ "
            f"oder unverändert freigeben mit „tagesrechnung anschrift {nummer} --wie-bestellt“"
        )
        ergebnis.fehler = True
        return ergebnis

    try:
        kunde = _kunde(kaeufer_daten, registriert, lieferung)
        ergebnis.positionen = _positionen(bestellung, nummer, ergebnis.zeit, kunde, einst, standard_ust_satz)
    except ValueError as e:
        ergebnis.grund, ergebnis.fehler = str(e), True
    return ergebnis


def _lieferadresse(bestellung: dict) -> dict:
    for anweisung in bestellung.get("fulfillmentStartInstructions") or []:
        ziel = ((anweisung or {}).get("shippingStep") or {}).get("shipTo")
        if ziel:
            return ziel
    return {}


def _kunde(kaeufer: dict, registriert: dict, lieferung: dict) -> Kunde:
    # Rechnungsanschrift ist die beim eBay-Konto hinterlegte Adresse, ersatzweise die Lieferanschrift
    email = (registriert.get("email") or lieferung.get("email") or "").strip()
    if "@" not in email:
        raise ValueError("eBay hat keine E-Mail-Adresse des Käufers geliefert (nur bis ca. 14 Tage nach dem Kauf)")
    quelle = registriert if registriert.get("contactAddress") else lieferung
    adresse = quelle.get("contactAddress") or {}
    name = (quelle.get("fullName") or registriert.get("fullName") or lieferung.get("fullName") or "").strip()
    benutzer = str(kaeufer.get("username") or "").strip()
    if not name:
        name = benutzer
    if not name:
        raise ValueError("Name des Käufers fehlt")
    strasse = ", ".join(t.strip() for t in (adresse.get("addressLine1"), adresse.get("addressLine2")) if t and t.strip())
    land = str(adresse.get("countryCode") or "").upper()
    return Kunde(
        schluessel=f"ebay:{benutzer.lower()}" if benutzer else email.lower(),
        kunden_nr="",
        name=name,
        email=email,
        firma=(quelle.get("companyName") or "").strip(),
        strasse=strasse,
        plz=str(adresse.get("postalCode") or "").strip(),
        ort=str(adresse.get("city") or "").strip(),
        land=LAENDER.get(land, land),
    )


def _positionen(
    bestellung: dict,
    nummer: str,
    zeit: datetime,
    kunde: Kunde,
    einst: EbayEinstellungen,
    standard_ust_satz: Decimal,
) -> list[Position]:
    def position(artikel: str, menge: Decimal, preis: Decimal, satz: Decimal, art_nr: str = "", einheit: str = ""):
        return Position(bestellnummer=nummer, bestellzeit=zeit, kunde=kunde, artikel=artikel, menge=menge,
                        einzelpreis=preis, ust_satz=satz, einheit=einheit, artikelnummer=art_nr)

    artikel = bestellung.get("lineItems") or []
    if not artikel:
        raise ValueError("Bestellung ohne Artikel")

    positionen: list[Position] = []
    wert_je_satz: dict[Decimal, Decimal] = defaultdict(Decimal)  # Warenwert nach Rabatt, für die Versandkosten
    rabatte: dict[Decimal, Decimal] = defaultdict(Decimal)
    versand_aus_positionen = Decimal(0)
    for zeile in artikel:
        try:
            menge = int(zeile.get("quantity") or 0)
        except (TypeError, ValueError):
            menge = 0
        if menge <= 0:
            raise ValueError(f"ungültige Menge bei {zeile.get('title')!r}")
        kosten = _betrag(zeile, "lineItemCost")  # Einzelpreis × Menge, vor Rabatten
        if kosten is None:
            raise ValueError(f"Preis fehlt bei {zeile.get('title')!r}")
        titel = str(zeile.get("title") or "Artikel").strip()
        sku = str(zeile.get("sku") or "").strip()
        art_nr = sku or str(zeile.get("legacyItemId") or "")
        satz = einst.ust_saetze.get(sku, standard_ust_satz) if sku else standard_ust_satz

        einzel = auf_cent(kosten / menge)
        if einzel * menge == kosten:
            positionen.append(position(titel, Decimal(menge), einzel, satz, art_nr, "Stk."))
        else:  # Gesamtpreis nicht auf den Cent teilbar → als eine Position mit Gesamtpreis
            positionen.append(position(f"{menge} × {titel}", Decimal(1), kosten, satz, art_nr))

        rabattiert = _betrag(zeile, "discountedLineItemCost")
        if rabattiert is not None and rabattiert != kosten:
            rabatte[satz] += rabattiert - kosten
        wert_je_satz[satz] += kosten if rabattiert is None else rabattiert
        versand_aus_positionen += _betrag(zeile.get("deliveryCost"), "shippingCost") or 0

    for satz, rabatt in rabatte.items():
        positionen.append(position("Rabatt", Decimal(1), rabatt, satz))

    summen = bestellung.get("pricingSummary") or {}
    versand = _betrag(summen, "deliveryCost")
    if versand is None:
        versand = versand_aus_positionen
    positionen += [position(text, Decimal(1), anteil, satz)
                   for text, anteil, satz in _anteilig(einst.versandkosten_text, versand, wert_je_satz)]

    # Abgleich mit dem Gesamtbetrag von eBay. Von eBay selbst erhobene Steuern (z. B. bei
    # Verkäufen ins Ausland) sind kein Umsatz des Verkäufers und gehören nicht auf die Rechnung.
    gesamt = _betrag(summen, "total")
    if gesamt is None:
        raise ValueError("eBay meldet keinen Gesamtbetrag")
    ziel = gesamt - (_betrag(summen, "tax") or 0)
    rest = ziel - sum((p.gesamt for p in positionen), Decimal(0))
    if rest:
        positionen += _rest_erklaeren(rest, summen, bool(rabatte), wert_je_satz, einst, position, gesamt)
    return positionen


def _anteilig(text: str, betrag: Decimal, wert_je_satz: dict[Decimal, Decimal]):
    """Versandkosten teilen das Schicksal der Ware: bei gemischten Steuersätzen anteilig aufteilen."""
    if not betrag:
        return []
    saetze = sorted(wert_je_satz, reverse=True)
    if len(saetze) == 1:
        return [(text, betrag, saetze[0])]
    gesamtwert = sum(wert_je_satz.values())
    if not gesamtwert:
        raise ValueError("Versandkosten lassen sich nicht auf die Steuersätze aufteilen")
    teile, verteilt = [], Decimal(0)
    for i, satz in enumerate(saetze):
        anteil = betrag - verteilt if i == len(saetze) - 1 else auf_cent(betrag * wert_je_satz[satz] / gesamtwert)
        verteilt += anteil
        teile.append((f"{text} (anteilig, {formate.zahl(satz)} % USt.)", anteil, satz))
    return teile


def _rest_erklaeren(rest, summen, rabatte_schon_erfasst, wert_je_satz, einst, position, gesamt) -> list[Position]:
    """Erklärt eine Differenz zum eBay-Gesamtbetrag mit Rabatt, Versandrabatt oder Preisanpassung.
    Lässt sie sich nicht erklären, wird die Bestellung nicht automatisch abgerechnet."""
    einziger_satz = next(iter(wert_je_satz)) if len(wert_je_satz) == 1 else None
    kandidaten = []
    rabatt = _betrag(summen, "priceDiscount")
    if rabatt and not rabatte_schon_erfasst:
        kandidaten.append(("Rabatt", -abs(rabatt)))
    versandrabatt = _betrag(summen, "deliveryDiscount")
    if versandrabatt:
        kandidaten.append(("Versandrabatt", -abs(versandrabatt)))
    anpassung = _betrag(summen, "adjustment")
    if anpassung:
        kandidaten.append(("Preisanpassung", anpassung))

    for anzahl in range(1, len(kandidaten) + 1):
        for auswahl in itertools.combinations(kandidaten, anzahl):
            if sum(b for _, b in auswahl) == rest:
                if einziger_satz is None:
                    raise ValueError(f"{', '.join(t for t, _ in auswahl)} bei gemischten Steuersätzen – "
                                     "bitte von Hand abrechnen")
                return [position(text, Decimal(1), b, einziger_satz) for text, b in auswahl]
    raise ValueError(
        f"Rechnungssumme passt nicht zum eBay-Gesamtbetrag {formate.betrag(gesamt)} "
        f"(Differenz {formate.betrag(rest)}) – bitte von Hand abrechnen"
    )


# --- Schnittstelle für quellen.laden und die Kommandozeile ----------------------------------------


def zeitraum_utc(von: date, bis: date, zeitzone: ZoneInfo, jetzt: datetime) -> tuple[datetime, datetime] | None:
    """Ganze Kalendertage in ``zeitzone`` als UTC-Zeitraum, höchstens bis ``jetzt``."""
    start = datetime.combine(von, uhrzeit(), zeitzone)
    ende = datetime.combine(bis + timedelta(days=1), uhrzeit(), zeitzone) - timedelta(milliseconds=1)
    ende = min(ende, jetzt)
    return (start, ende) if start <= ende else None


def abrufen(
    einst: EbayEinstellungen, von: date, bis: date, zeitzone: ZoneInfo, standard_ust_satz: Decimal,
    jetzt: datetime | None = None, konto: EbayKonto | None = None,
    freigegeben: frozenset[str] | set[str] = frozenset(),
) -> list[EbayBestellung]:
    konto = konto or EbayKonto(einst)
    zeitraum = zeitraum_utc(von, bis, zeitzone, jetzt or datetime.now(timezone.utc))
    if zeitraum is None:
        return []
    bestellungen = konto.bestellungen(*zeitraum)
    _freigabe_pruefen(konto)
    return [auswerten(b, einst, zeitzone, standard_ust_satz, freigegeben) for b in bestellungen]


def _freigabe_pruefen(konto: EbayKonto) -> None:
    bis = konto.freigabe_bis()
    if bis and bis - datetime.now(timezone.utc) < timedelta(days=WARNEN_TAGE_VOR_ABLAUF):
        log.warning("Die eBay-Freigabe läuft am %s ab. Bitte rechtzeitig erneuern: tagesrechnung ebay-verbinden",
                    formate.datum(bis.date()))
