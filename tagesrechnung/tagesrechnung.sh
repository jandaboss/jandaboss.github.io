#!/bin/bash
# Startet Tagesrechnung mit der config.toml aus diesem Ordner.
#
#   ./tagesrechnung.sh ebay-verbinden
#   ./tagesrechnung.sh ebay-pruefen --tage 14
#   ./tagesrechnung.sh ausfuehren --probelauf
#
# Die Passwörter (eBay-Cert-ID, SMTP-Passwort) kommen aus dem macOS-Schlüsselbund
# (eingerichtet mit: bash deploy/macos/einrichten.sh) oder aus Umgebungsvariablen.
# Bewusst ohne "set -u": die Bash 3.2 von macOS meldet sonst bei leerem "$@" einen Fehler.
set -e

ORDNER="$(cd "$(dirname "$0")" && pwd)"
PROGRAMM="$ORDNER/.venv/bin/tagesrechnung"
KONTO="${USER:-$(id -un)}"

if [ ! -x "$PROGRAMM" ]; then
  echo "Tagesrechnung ist noch nicht eingerichtet. Bitte zuerst ausführen: bash deploy/macos/einrichten.sh" >&2
  exit 2
fi

aus_schluesselbund() {
  # $1 = Name der Umgebungsvariable, $2 = Eintrag im Schlüsselbund
  if [ -z "$(printenv "$1" || true)" ] && command -v security >/dev/null 2>&1; then
    wert="$(security find-generic-password -a "$KONTO" -s "$2" -w 2>/dev/null || true)"
    if [ -n "$wert" ]; then
      export "$1=$wert"
    fi
  fi
}
aus_schluesselbund TAGESRECHNUNG_EBAY_SECRET tagesrechnung-ebay
aus_schluesselbund TAGESRECHNUNG_SMTP_PASSWORT tagesrechnung-smtp

cd "$ORDNER"
if [ $# -eq 0 ]; then
  exec "$PROGRAMM" --help
fi
case "$1" in
  -*) exec "$PROGRAMM" "$@" ;;  # --help, --version
esac
befehl="$1"
shift
exec "$PROGRAMM" "$befehl" -c "$ORDNER/config.toml" "$@"
