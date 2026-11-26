"""Gemeinsame Einstellungen für sichere Verbindungen (eBay, Mailserver)."""

from __future__ import annotations

import ssl


def ssl_kontext() -> ssl.SSLContext:
    """TLS-Kontext mit den Zertifikaten des Systems und – falls installiert – denen von certifi.

    Das Python von python.org bringt unter macOS keine Systemzertifikate mit; ohne certifi
    schlügen dort alle Verbindungen zu eBay und zum Mailserver mit CERTIFICATE_VERIFY_FAILED fehl.
    """
    kontext = ssl.create_default_context()
    try:
        import certifi

        kontext.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return kontext
