"""Erzeugt die Rechnung als PDF (A4, Anschriftfeld passend für Fensterumschläge nach DIN 5008).

Enthält die Pflichtangaben nach § 14 Abs. 4 UStG: Name und Anschrift von Unternehmen und Kunde,
Steuernummer oder USt-IdNr., Rechnungsdatum, fortlaufende Rechnungsnummer, Menge und Art der
Lieferung, Liefer-/Leistungsdatum, Entgelt nach Steuersätzen aufgeschlüsselt, Steuersatz und
Steuerbetrag bzw. Hinweis auf die Steuerbefreiung (§ 19 UStG).
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from . import formate
from .konfiguration import Konfiguration
from .modelle import Rechnung
from .vorlagen import werte_fuer_rechnung

BREITE, HOEHE = A4
LINKS = 25 * mm
RECHTS = 20 * mm
INHALT = BREITE - LINKS - RECHTS
FUSS = 32 * mm  # Höhe des Fußbereichs
GRAU = colors.HexColor("#666666")
HELLGRAU = colors.HexColor("#d9d9d9")
HINTERGRUND = colors.HexColor("#f2f2f2")


def erzeugen(rechnung: Rechnung, konfig: Konfiguration, ziel: Path) -> None:
    """Schreibt die Rechnung als PDF nach ``ziel`` (atomar über eine temporäre Datei)."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    normal, fett = _schriften(konfig)
    werte = werte_fuer_rechnung(rechnung, konfig)
    firma = konfig.firma
    einst = konfig.rechnung

    stil = ParagraphStyle("normal", fontName=normal, fontSize=10, leading=14)
    klein = ParagraphStyle("klein", parent=stil, fontSize=9, leading=11.5)
    rechts = ParagraphStyle("rechts", parent=klein, alignment=TA_RIGHT)
    kopf = ParagraphStyle("kopf", parent=klein, fontName=fett)
    kopf_rechts = ParagraphStyle("kopf_rechts", parent=kopf, alignment=TA_RIGHT)
    titel = ParagraphStyle("titel", parent=stil, fontName=fett, fontSize=15, leading=19, spaceAfter=4 * mm)

    def absatz(text: str, s: ParagraphStyle = stil) -> Paragraph:
        return Paragraph(escape(text).replace("\n", "<br/>"), s)

    preisart = "brutto" if einst.preise_sind == "brutto" else "netto"
    mit_ust = not einst.kleinunternehmer
    symbol = einst.waehrungssymbol

    # --- Positionstabelle ---------------------------------------------------------------------
    kopfzeile = [
        Paragraph("Pos.", kopf),
        Paragraph("Bezeichnung", kopf),
        Paragraph("Menge", kopf_rechts),
        Paragraph(f"Einzelpreis{'<br/>' + preisart if mit_ust else ''}", kopf_rechts),
        Paragraph("USt.", kopf_rechts),
        Paragraph(f"Gesamt{'<br/>' + preisart if mit_ust else ''}", kopf_rechts),
    ]
    breiten = [10 * mm, 73 * mm, 20 * mm, 24 * mm, 12 * mm, 26 * mm]
    if not mit_ust:
        del kopfzeile[4]
        breiten = [10 * mm, 85 * mm, 20 * mm, 24 * mm, 26 * mm]
    spalten = len(breiten)

    daten: list[list] = [kopfzeile]
    format_befehle: list[tuple] = []
    pos = 0
    for bestellung in rechnung.bestellungen:
        zeit = bestellung.zeit
        uhrzeit = f", {zeit:%H:%M} Uhr" if (zeit.hour, zeit.minute) != (0, 0) else ""
        zeile = len(daten)
        daten.append(
            [Paragraph(f"<b>Bestellung {escape(bestellung.nummer)}</b> vom {formate.datum(zeit)}{uhrzeit}", klein)]
            + [""] * (spalten - 1)
        )
        format_befehle += [("SPAN", (0, zeile), (-1, zeile)), ("BACKGROUND", (0, zeile), (-1, zeile), HINTERGRUND)]
        for p in bestellung.positionen:
            pos += 1
            bezeichnung = escape(p.artikel)
            if p.artikelnummer:
                bezeichnung += f'<br/><font size="7.5" color="#666666">Art.-Nr. {escape(p.artikelnummer)}</font>'
            menge = f"{formate.zahl(p.menge)} {p.einheit}".strip()
            zeile = [
                Paragraph(str(pos), klein),
                Paragraph(bezeichnung, klein),
                Paragraph(escape(menge), rechts),
                Paragraph(formate.betrag(p.einzelpreis, symbol), rechts),
                Paragraph(f"{formate.zahl(p.ust_satz)} %", rechts),
                Paragraph(formate.betrag(p.gesamt, symbol), rechts),
            ]
            if not mit_ust:
                del zeile[4]
            daten.append(zeile)

    positionen = Table(daten, colWidths=breiten, repeatRows=1)
    positionen.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, HELLGRAU),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
            + format_befehle
        )
    )

    # --- Summen ---------------------------------------------------------------------------------
    s = rechnung.summen
    summenzeilen: list[list[str]] = []
    if mit_ust:
        summenzeilen.append(["Summe netto", formate.betrag(s.netto, symbol)])
        for g in s.gruppen:
            summenzeilen.append(
                [f"zzgl. {formate.zahl(g.satz)} % USt. auf {formate.betrag(g.netto, symbol)}",
                 formate.betrag(g.steuer, symbol)]
            )
    summenzeilen.append(["Gesamtbetrag", formate.betrag(s.brutto, symbol)])
    summen = Table(summenzeilen, colWidths=[75 * mm, 30 * mm], hAlign="RIGHT")
    summen.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), normal),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), fett),
                ("FONTSIZE", (0, -1), (-1, -1), 11),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
                ("LINEBELOW", (0, -1), (-1, -1), 0.8, colors.black),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, -1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 4),
            ]
        )
    )

    inhalt = [
        NextPageTemplate("folgeseite"),
        Paragraph(f"Rechnung {escape(rechnung.nummer)}", titel),
        absatz(einst.einleitung.format_map(werte)),
        Spacer(1, 5 * mm),
        positionen,
        Spacer(1, 4 * mm),
        KeepTogether([summen]),
        Spacer(1, 7 * mm),
    ]
    if einst.kleinunternehmer:
        inhalt += [absatz("Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."), Spacer(1, 3 * mm)]
    if einst.zahlungshinweis:
        inhalt += [absatz(einst.zahlungshinweis.format_map(werte)), Spacer(1, 3 * mm)]
    if einst.schlusstext:
        inhalt.append(absatz(einst.schlusstext.format_map(werte)))

    # --- Seitenvorlagen ---------------------------------------------------------------------------
    unten = FUSS + 8 * mm
    erste = Frame(LINKS, unten, INHALT, HOEHE - 95 * mm - unten, id="erste", leftPadding=0, rightPadding=0)
    folge = Frame(LINKS, unten, INHALT, HOEHE - 28 * mm - unten, id="folge", leftPadding=0, rightPadding=0)

    def fuss(c: rl_canvas.Canvas, _doc) -> None:
        _fusszeile(c, konfig, normal)

    def erste_seite(c: rl_canvas.Canvas, doc) -> None:
        _briefkopf(c, rechnung, konfig, normal, fett)
        fuss(c, doc)

    def folgeseite(c: rl_canvas.Canvas, doc) -> None:
        c.setFont(normal, 8)
        c.setFillColor(GRAU)
        c.drawString(LINKS, HOEHE - 17 * mm, f"{firma.name} · Rechnung {rechnung.nummer}")
        fuss(c, doc)

    temp = ziel.with_name(ziel.name + ".tmp")
    doc = BaseDocTemplate(
        str(temp),
        pagesize=A4,
        title=f"Rechnung {rechnung.nummer}",
        author=firma.name,
        subject=f"Rechnung {rechnung.nummer} für {rechnung.kunde.firma or rechnung.kunde.name}",
        creator="Tagesrechnung",
        pageTemplates=[
            PageTemplate(id="erste", frames=[erste], onPage=erste_seite),
            PageTemplate(id="folgeseite", frames=[folge], onPage=folgeseite),
        ],
    )
    try:
        doc.build(inhalt, canvasmaker=functools.partial(_SeitenzahlCanvas, schrift=normal))
        os.replace(temp, ziel)
    finally:
        temp.unlink(missing_ok=True)


def _schriften(konfig: Konfiguration) -> tuple[str, str]:
    einst = konfig.rechnung
    if not einst.schrift:
        return "Helvetica", "Helvetica-Bold"
    pdfmetrics.registerFont(TTFont("Rechnung", str(einst.schrift)))
    fett = "Rechnung"
    if einst.schrift_fett:
        pdfmetrics.registerFont(TTFont("Rechnung-Fett", str(einst.schrift_fett)))
        fett = "Rechnung-Fett"
    return "Rechnung", fett


def _briefkopf(c: rl_canvas.Canvas, rechnung: Rechnung, konfig: Konfiguration, normal: str, fett: str) -> None:
    firma = konfig.firma
    c.saveState()

    # Logo bzw. Firmenname oben rechts
    if firma.logo:
        bild = ImageReader(str(firma.logo))
        b, h = bild.getSize()
        faktor = min(60 * mm / b, 22 * mm / h)
        c.drawImage(bild, BREITE - RECHTS - b * faktor, HOEHE - 12 * mm - h * faktor, b * faktor, h * faktor,
                    mask="auto")
    else:
        c.setFont(fett, 15)
        c.drawRightString(BREITE - RECHTS, HOEHE - 22 * mm, firma.name)
        c.setFont(normal, 8.5)
        c.setFillColor(GRAU)
        unterzeile = " · ".join(t for t in (firma.webseite, firma.email, firma.telefon) if t)
        if unterzeile:
            c.drawRightString(BREITE - RECHTS, HOEHE - 27 * mm, unterzeile)

    # Rücksendeangabe und Anschrift (Anschriftfeld DIN 5008 Form B: 45 mm von oben, 20 mm von links)
    c.setFillColor(GRAU)
    c.setFont(normal, 7)
    absender = firma.absenderzeile()
    c.drawString(LINKS, HOEHE - 50 * mm, absender)
    c.setLineWidth(0.3)
    c.setStrokeColor(GRAU)
    c.line(LINKS, HOEHE - 50.8 * mm, LINKS + pdfmetrics.stringWidth(absender, normal, 7), HOEHE - 50.8 * mm)

    c.setFillColor(colors.black)
    c.setFont(normal, 10)
    y = HOEHE - 58 * mm
    anschrift = rechnung.kunde.anschrift()
    if rechnung.kunde.land and _land(rechnung.kunde.land) == _land(firma.land or "Deutschland"):
        anschrift.remove(rechnung.kunde.land)  # Inlandsanschrift ohne Länderangabe (DIN 5008)
    for zeile in anschrift:
        for teil in simpleSplit(zeile, normal, 10, 85 * mm):
            c.drawString(LINKS, y, teil)
            y -= 4.6 * mm

    # Informationsblock rechts
    info = [
        ("Rechnungsnummer", rechnung.nummer),
        ("Rechnungsdatum", formate.datum(rechnung.rechnungsdatum)),
        ("Liefer-/Leistungsdatum", formate.datum(rechnung.leistungsdatum)),
    ]
    if rechnung.kunde.kunden_nr:
        info.append(("Kundennummer", rechnung.kunde.kunden_nr))
    if rechnung.kunde.ust_idnr:
        info.append(("Ihre USt-IdNr.", rechnung.kunde.ust_idnr))
    if konfig.rechnung.zahlungsziel_tage > 0:
        info.append(("Zahlbar bis", formate.datum(rechnung.faellig)))
    y = HOEHE - 52 * mm
    for name, wert in info:
        c.setFont(normal, 8.5)
        c.setFillColor(GRAU)
        c.drawString(122 * mm, y, name)
        c.setFont(normal, 9)
        c.setFillColor(colors.black)
        c.drawRightString(BREITE - RECHTS, y, wert)
        y -= 4.8 * mm
    c.restoreState()


def _land(name: str) -> str:
    name = name.strip().lower()
    return "deutschland" if name in {"de", "deu", "germany", "deutschland"} else name


def _fusszeile(c: rl_canvas.Canvas, konfig: Konfiguration, normal: str) -> None:
    f = konfig.firma
    spalten = [
        [f.name, f.strasse, f"{f.plz} {f.ort}".strip(), f.land],
        [f"Tel. {f.telefon}" if f.telefon else "", f.email, f.webseite],
        [f.bank, f"IBAN {f.iban}" if f.iban else "", f"BIC {f.bic}" if f.bic else ""],
        [
            f"Steuernummer {f.steuernummer}" if f.steuernummer else "",
            f"USt-IdNr. {f.ust_idnr}" if f.ust_idnr else "",
            f"Geschäftsführung: {f.geschaeftsfuehrung}" if f.geschaeftsfuehrung else "",
            f.handelsregister,
        ],
    ]
    spalten = [[z for z in s if z] for s in spalten]
    spalten = [s for s in spalten if s]
    if not spalten:
        return
    c.saveState()
    c.setStrokeColor(HELLGRAU)
    c.setLineWidth(0.5)
    c.line(LINKS, FUSS, BREITE - RECHTS, FUSS)
    c.setFont(normal, 7)
    c.setFillColor(GRAU)
    # Spaltenbreiten nach Inhalt verteilen, damit z. B. die IBAN nicht umbricht
    abstand = 4 * mm
    verfuegbar = INHALT - abstand * (len(spalten) - 1)
    natuerlich = [max(pdfmetrics.stringWidth(z, normal, 7) for z in s) for s in spalten]
    if sum(natuerlich) <= verfuegbar:
        rest = (verfuegbar - sum(natuerlich)) / len(spalten)
        breiten = [b + rest for b in natuerlich]
    else:
        breiten = [verfuegbar * b / sum(natuerlich) for b in natuerlich]
    x = LINKS
    for spalte, breite in zip(spalten, breiten):
        y = FUSS - 4 * mm
        for zeile in spalte:
            for teil in simpleSplit(zeile, normal, 7, breite):
                c.drawString(x, y, teil)
                y -= 3.2 * mm
        x += breite + abstand
    c.restoreState()


class _SeitenzahlCanvas(rl_canvas.Canvas):
    """Canvas, der nach dem Aufbau aller Seiten „Seite x von y“ einträgt."""

    def __init__(self, *args, schrift: str = "Helvetica", **kwargs):
        super().__init__(*args, **kwargs)
        self._schrift = schrift
        self._seiten: list[dict] = []

    def showPage(self):  # noqa: N802 (reportlab-API)
        self._seiten.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        gesamt = len(self._seiten)
        for zustand in self._seiten:
            self.__dict__.update(zustand)
            self.setFont(self._schrift, 8)
            self.setFillColor(GRAU)
            self.drawRightString(BREITE - RECHTS, FUSS + 3 * mm, f"Seite {self._pageNumber} von {gesamt}")
            super().showPage()
        super().save()
