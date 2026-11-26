"""E-Mail-Versand per SMTP – und ein Probelauf-Versand, der die Mails nur als .eml-Dateien ablegt."""

from __future__ import annotations

import logging
import os
import smtplib
import socket
import ssl
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from . import formate
from .konfiguration import EmailEinstellungen, Konfiguration
from .modelle import GespeicherteRechnung
from .netz import ssl_kontext
from .vorlagen import werte_fuer_gespeicherte

log = logging.getLogger(__name__)


class VersandFehler(Exception):
    pass


def _adresse(email: str, name: str = "") -> Address:
    try:
        return Address(display_name=name, addr_spec=email.strip())
    except (ValueError, HeaderParseError, IndexError) as fehler:
        raise VersandFehler(f"ungültige E-Mail-Adresse {email!r} ({fehler})") from None


def _grundgeruest(e: EmailEinstellungen, absender_name: str) -> EmailMessage:
    nachricht = EmailMessage()
    nachricht["From"] = _adresse(e.absender, e.absender_name or absender_name)
    if e.antwort_an:
        nachricht["Reply-To"] = _adresse(e.antwort_an)
    nachricht["Date"] = formatdate(localtime=True)
    nachricht["Message-ID"] = make_msgid(domain=e.absender.rpartition("@")[2])
    return nachricht


def nachricht_erstellen(konfig: Konfiguration, rechnung: GespeicherteRechnung, pdf: Path) -> EmailMessage:
    e = konfig.email
    werte = werte_fuer_gespeicherte(rechnung, konfig)
    nachricht = _grundgeruest(e, konfig.firma.name)
    nachricht["To"] = _adresse(rechnung.kunde_email, rechnung.kunde_name)
    if e.bcc:
        nachricht["Bcc"] = [_adresse(a) for a in e.bcc]
    nachricht["Subject"] = e.betreff.format_map(werte)
    nachricht.set_content(e.text.format_map(werte))
    try:
        inhalt = pdf.read_bytes()
    except OSError as fehler:
        raise VersandFehler(f"PDF kann nicht gelesen werden: {pdf} ({fehler.strerror})") from None
    nachricht.add_attachment(
        inhalt,
        maintype="application",
        subtype="pdf",
        filename=f"Rechnung_{formate.dateiname(rechnung.nummer)}.pdf",
    )
    return nachricht


def probenachricht(konfig: Konfiguration, empfaenger: str) -> EmailMessage:
    nachricht = _grundgeruest(konfig.email, konfig.firma.name)
    nachricht["To"] = _adresse(empfaenger)
    nachricht["Subject"] = "Testnachricht von Tagesrechnung"
    nachricht.set_content(
        "Dies ist eine Testnachricht.\n\nWenn Sie diese E-Mail lesen, funktioniert der E-Mail-Versand "
        "für die Tagesrechnungen.\n"
    )
    return nachricht


class SmtpVersand:
    """Versendet über einen SMTP-Server. Die Verbindung wird für alle Mails eines Laufs wiederverwendet."""

    def __init__(self, einstellungen: EmailEinstellungen):
        self.e = einstellungen
        self._smtp: smtplib.SMTP | None = None
        self._dauerhafter_fehler: str | None = None
        self.probelauf = False

    def __enter__(self) -> "SmtpVersand":
        return self

    def __exit__(self, *_exc) -> None:
        self.schliessen()

    def schliessen(self) -> None:
        if self._smtp is not None:
            try:
                self._smtp.quit()
            except (smtplib.SMTPException, OSError):
                pass
            self._smtp = None

    def _verbinden(self) -> smtplib.SMTP:
        e = self.e
        if not e.smtp_server:
            raise VersandFehler("[email] smtp_server ist nicht angegeben")
        kontext = ssl_kontext()
        if e.sicherheit == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(e.smtp_server, e.smtp_port, timeout=e.zeitlimit_sekunden,
                                                  context=kontext)
        else:
            smtp = smtplib.SMTP(e.smtp_server, e.smtp_port, timeout=e.zeitlimit_sekunden)
            if e.sicherheit == "starttls":
                smtp.starttls(context=kontext)
        if e.benutzer:
            passwort = os.environ.get(e.passwort_env) or e.passwort
            if not passwort:
                smtp.close()
                self._dauerhafter_fehler = (
                    f"Kein SMTP-Passwort: Umgebungsvariable {e.passwort_env} setzen oder [email] passwort angeben"
                )
                raise VersandFehler(self._dauerhafter_fehler)
            smtp.login(e.benutzer, passwort)
        return smtp

    def senden(self, nachricht: EmailMessage, kennung: str) -> None:
        if self._dauerhafter_fehler:
            raise VersandFehler(self._dauerhafter_fehler)
        try:
            if self._smtp is None:
                self._smtp = self._verbinden()
            self._smtp.send_message(nachricht)
        except VersandFehler:
            raise
        except (smtplib.SMTPException, OSError) as fehler:
            beschreibung = _beschreiben(fehler, self.e)
            if isinstance(fehler, (smtplib.SMTPAuthenticationError, ssl.SSLError)):
                # Würde bei jeder weiteren Mail genauso scheitern
                self._dauerhafter_fehler = beschreibung
            self.schliessen()  # beim nächsten Versuch neu verbinden
            raise VersandFehler(beschreibung) from fehler


class ProbeVersand:
    """Versendet nichts, sondern speichert jede Mail als .eml-Datei (lässt sich mit jedem Mailprogramm öffnen)."""

    probelauf = True

    def __init__(self, ordner: Path):
        self.ordner = ordner

    def __enter__(self) -> "ProbeVersand":
        return self

    def __exit__(self, *_exc) -> None:
        pass

    def senden(self, nachricht: EmailMessage, kennung: str) -> None:
        self.ordner.mkdir(parents=True, exist_ok=True)
        (self.ordner / f"{formate.dateiname(kennung)}.eml").write_bytes(bytes(nachricht))


def _beschreiben(fehler: Exception, e: EmailEinstellungen) -> str:
    def antwort(f) -> str:
        text = f.smtp_error.decode(errors="replace") if isinstance(f.smtp_error, bytes) else str(f.smtp_error)
        return f"{f.smtp_code} {text}".strip()

    if isinstance(fehler, smtplib.SMTPAuthenticationError):
        return f"Anmeldung am SMTP-Server fehlgeschlagen ({antwort(fehler)}) – Benutzer/Passwort prüfen"
    if isinstance(fehler, smtplib.SMTPRecipientsRefused):
        teile = []
        for adresse, (code, text) in fehler.recipients.items():
            text = text.decode(errors="replace") if isinstance(text, bytes) else str(text)
            teile.append(f"{adresse} ({code} {text})")
        return "Empfänger abgelehnt: " + "; ".join(teile)
    if isinstance(fehler, smtplib.SMTPSenderRefused):
        return f"Absender {e.absender} abgelehnt ({antwort(fehler)})"
    if isinstance(fehler, smtplib.SMTPResponseException):
        return f"SMTP-Fehler {antwort(fehler)}"
    if isinstance(fehler, ssl.SSLError):
        return f"TLS-Fehler ({fehler}) – passt [email] sicherheit (starttls/ssl) zum Port {e.smtp_port}?"
    if isinstance(fehler, (socket.timeout, TimeoutError)):
        return f"Zeitüberschreitung bei der Verbindung zu {e.smtp_server}:{e.smtp_port}"
    if isinstance(fehler, socket.gaierror):
        return f"SMTP-Server {e.smtp_server!r} nicht gefunden"
    if isinstance(fehler, ConnectionRefusedError):
        return f"Verbindung zu {e.smtp_server}:{e.smtp_port} abgelehnt"
    return f"{type(fehler).__name__}: {fehler}"
