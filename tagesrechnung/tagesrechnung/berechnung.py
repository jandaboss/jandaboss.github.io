"""Fasst Positionen zu Bestellungen und Bestellungen zu Rechnungen zusammen und berechnet die Summen."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal

from .formate import auf_cent
from .modelle import Bestellung, Position, Steuergruppe, Summen

NULL = Decimal("0.00")


def bestellungen_bilden(positionen: Iterable[Position]) -> tuple[list[Bestellung], list[str]]:
    """Gruppiert Positionen nach Bestellnummer. Liefert (Bestellungen, Fehlermeldungen)."""
    nach_nummer: dict[str, list[Position]] = {}
    for p in positionen:
        nach_nummer.setdefault(p.bestellnummer, []).append(p)

    bestellungen: list[Bestellung] = []
    fehler: list[str] = []
    for nummer, liste in nach_nummer.items():
        kunden = {p.kunde.schluessel for p in liste}
        if len(kunden) > 1:
            fehler.append(
                f"Bestellung {nummer}: Positionen gehören zu verschiedenen Kunden ({', '.join(sorted(kunden))}) "
                "– Bestellung übersprungen"
            )
            continue
        tage = {p.bestellzeit.date() for p in liste}
        if len(tage) > 1:
            fehler.append(f"Bestellung {nummer}: Positionen haben verschiedene Bestelldaten – Bestellung übersprungen")
            continue
        lieferdaten = [p.lieferdatum for p in liste if p.lieferdatum]
        bestellungen.append(
            Bestellung(nummer=nummer, zeit=min(p.bestellzeit for p in liste), kunde=liste[0].kunde, positionen=liste,
                       lieferdatum=max(lieferdaten) if lieferdaten else None)
        )
    bestellungen.sort(key=lambda b: (b.zeit, b.nummer))
    return bestellungen, fehler


def gruppieren(bestellungen: Iterable[Bestellung], eine_rechnung_pro: str) -> list[list[Bestellung]]:
    """Teilt Bestellungen auf Rechnungen auf.

    ``"kunde"``: eine Sammelrechnung je Kunde und Bestelltag.
    ``"bestellung"``: eine Rechnung je Bestellung.
    """
    sortiert = sorted(bestellungen, key=lambda b: (b.zeit, b.nummer))
    if eine_rechnung_pro == "bestellung":
        return [[b] for b in sortiert]
    gruppen: dict[tuple, list[Bestellung]] = {}
    for b in sortiert:
        gruppen.setdefault((b.zeit.date(), b.kunde.schluessel), []).append(b)
    return list(gruppen.values())


def summen_berechnen(positionen: Iterable[Position], preise_sind: str, kleinunternehmer: bool) -> Summen:
    """Summen je Steuersatz und gesamt.

    Bei Bruttopreisen wird die Steuer je Steuersatz aus der Bruttosumme herausgerechnet,
    bei Nettopreisen auf die Nettosumme aufgeschlagen. Kleinunternehmer weisen keine
    Umsatzsteuer aus (§ 19 UStG).
    """
    je_satz: dict[Decimal, Decimal] = defaultdict(lambda: NULL)
    for p in positionen:
        satz = Decimal(0) if kleinunternehmer else p.ust_satz
        je_satz[satz.normalize()] += p.gesamt

    gruppen: list[Steuergruppe] = []
    for satz in sorted(je_satz, reverse=True):
        summe = je_satz[satz]
        if kleinunternehmer or satz == 0:
            netto, steuer = summe, NULL
        elif preise_sind == "brutto":
            netto = auf_cent(summe * 100 / (100 + satz))
            steuer = summe - netto
        else:
            netto = summe
            steuer = auf_cent(summe * satz / 100)
        gruppen.append(Steuergruppe(satz=satz, netto=netto, steuer=steuer, brutto=netto + steuer))

    return Summen(
        netto=sum((g.netto for g in gruppen), NULL),
        steuer=sum((g.steuer for g in gruppen), NULL),
        brutto=sum((g.brutto for g in gruppen), NULL),
        gruppen=tuple(gruppen),
    )
