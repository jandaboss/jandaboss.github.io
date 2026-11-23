"""Zahlen, Beträge und Datumsangaben lesen und im deutschen Format ausgeben."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

CENT = Decimal("0.01")

_DATUMSFORMATE = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%d.%m.%y",
)


def auf_cent(wert: Decimal) -> Decimal:
    """Kaufmännisch auf ganze Cent runden."""
    return wert.quantize(CENT, rounding=ROUND_HALF_UP)


def zahl_lesen(wert: object) -> Decimal:
    """Liest Zahlen wie ``12,50``, ``1.234,56``, ``1,234.56``, ``19 %`` oder ``9,99 €``."""
    if isinstance(wert, Decimal):
        return wert
    if isinstance(wert, bool):
        raise ValueError(f"keine gültige Zahl: {wert!r}")
    if isinstance(wert, int):
        return Decimal(wert)
    if isinstance(wert, float):
        return Decimal(str(wert))

    text = re.sub(r"(?i)\s|€|eur|%", "", str(wert or ""))
    if not text:
        raise ValueError("Wert fehlt")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):  # 1.234,56
            text = text.replace(".", "").replace(",", ".")
        else:  # 1,234.56
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        zahl = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"keine gültige Zahl: {wert!r}") from None
    if not zahl.is_finite():
        raise ValueError(f"keine gültige Zahl: {wert!r}")
    return zahl


def zeitpunkt_lesen(wert: object, zeitzone: ZoneInfo) -> datetime:
    """Liest ein Bestelldatum und gibt es als lokale (naive) Zeit in ``zeitzone`` zurück.

    Akzeptiert ISO-Formate (``2026-11-22``, ``2026-11-22 14:35``, ``2026-11-22T14:35:00+01:00``),
    deutsche Formate (``22.11.2026``, ``22.11.2026 14:35``) und Unix-Zeitstempel.
    Angaben mit Zeitzone werden in ``zeitzone`` umgerechnet, Angaben ohne gelten als Ortszeit.
    """
    if isinstance(wert, datetime):
        zeit = wert
    elif isinstance(wert, date):
        zeit = datetime.combine(wert, time())
    elif isinstance(wert, (int, float)) and not isinstance(wert, bool):
        zeit = datetime.fromtimestamp(wert, zeitzone)
    else:
        text = str(wert or "").strip()
        if not text:
            raise ValueError("Bestelldatum fehlt")
        try:
            zeit = datetime.fromisoformat(text)
        except ValueError:
            for muster in _DATUMSFORMATE:
                try:
                    zeit = datetime.strptime(text, muster)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(f"unbekanntes Datumsformat: {text!r}") from None
    if zeit.tzinfo is not None:
        zeit = zeit.astimezone(zeitzone).replace(tzinfo=None)
    return zeit


def datum_lesen(text: str, heute: date) -> date:
    """Liest ``heute``, ``gestern``, ``JJJJ-MM-TT`` oder ``TT.MM.JJJJ``."""
    eingabe = text.strip().lower()
    if eingabe == "heute":
        return heute
    if eingabe == "gestern":
        return date.fromordinal(heute.toordinal() - 1)
    for muster in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(eingabe, muster).date()
        except ValueError:
            continue
    raise ValueError(f"Datum nicht erkannt: {text!r} (erlaubt: heute, gestern, JJJJ-MM-TT, TT.MM.JJJJ)")


def betrag(wert: Decimal, symbol: str = "€") -> str:
    """``Decimal('1234.5')`` → ``'1.234,50 €'``"""
    gerundet = auf_cent(wert)
    vorzeichen = "-" if gerundet < 0 else ""
    text = f"{abs(gerundet):,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return f"{vorzeichen}{text} {symbol}".rstrip()


def zahl(wert: Decimal) -> str:
    """Menge oder Steuersatz ohne überflüssige Nachkommastellen: ``Decimal('1.50')`` → ``'1,5'``"""
    text = format(wert, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def datum(wert: date) -> str:
    return wert.strftime("%d.%m.%Y")


def dateiname(text: str) -> str:
    """Macht aus einer Rechnungsnummer einen sicheren Dateinamen."""
    return re.sub(r"[^\w.-]+", "_", text).strip("._") or "rechnung"
