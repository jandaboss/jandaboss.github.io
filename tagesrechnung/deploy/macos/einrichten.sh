#!/bin/bash
# Richtet Tagesrechnung auf dem Mac ein:
#   1. Python-Umgebung und Programm installieren
#   2. eBay-Cert-ID und SMTP-Passwort im macOS-Schlüsselbund speichern (statt in Dateien)
#
# Aufruf im Programmordner (nicht unter Dokumente, Schreibtisch oder Downloads ablegen –
# dort darf die macOS-Zeitsteuerung später nicht zugreifen):
#   bash deploy/macos/einrichten.sh
set -e
set -o pipefail

cd "$(dirname "$0")/../.."
ORDNER="$(pwd)"
KONTO="${USER:-$(id -un)}"
echo "== Tagesrechnung einrichten in $ORDNER"

case "$ORDNER" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*|"$HOME/Library/Mobile Documents"*)
    echo "Achtung: Dieser Ordner liegt unter Dokumente, Schreibtisch, Downloads oder iCloud."
    echo "Dort darf der tägliche Lauf von macOS aus nicht zugreifen. Bitte den Ordner z. B. nach"
    echo "  $HOME/tagesrechnung"
    echo "verschieben und dieses Skript dort erneut starten."
    exit 1
    ;;
esac

# --- 1. Python 3.11 oder neuer finden (nicht das veraltete /usr/bin/python3 von macOS) ---------------
PY=""
for kandidat in python3.14 python3.13 python3.12 python3.11 python3; do
  pfad="$(command -v "$kandidat" 2>/dev/null || true)"
  if [ -z "$pfad" ] || [ "$pfad" = "/usr/bin/python3" ]; then
    continue
  fi
  if "$pfad" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$pfad"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 oder neuer wurde nicht gefunden."
  echo "Bitte den Installer von https://www.python.org/downloads/macos/ herunterladen, installieren"
  echo "und danach dieses Skript erneut starten."
  exit 1
fi
echo "Python: $("$PY" --version) ($PY)"

# --- 2. Programm installieren ---------------------------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet --upgrade .
chmod +x tagesrechnung.sh deploy/macos/*.sh
echo "Programm installiert: $(.venv/bin/tagesrechnung --version)"

# --- 3. Passwörter im Schlüsselbund ----------------------------------------------------------------
speichern() {
  # $1 = Eintrag im Schlüsselbund, $2 = Beschreibung
  if ! command -v security >/dev/null 2>&1; then
    echo "Kein macOS-Schlüsselbund gefunden – Passwort bitte als Umgebungsvariable setzen ($2)."
    return 0
  fi
  if security find-generic-password -a "$KONTO" -s "$1" >/dev/null 2>&1; then
    printf "Schon im Schlüsselbund: %s. Ersetzen? (j/N) " "$2"
    read -r antwort
    if [ "$antwort" != "j" ] && [ "$antwort" != "J" ]; then
      return 0
    fi
  else
    printf "Im Schlüsselbund speichern: %s? (J/n) " "$2"
    read -r antwort
    if [ "$antwort" = "n" ] || [ "$antwort" = "N" ]; then
      echo "Übersprungen – später einfach dieses Skript erneut starten."
      return 0
    fi
  fi
  echo "Bitte $2 eingeben. macOS fragt zweimal, die Eingabe bleibt unsichtbar:"
  # -w am Ende: security fragt den Wert selbst ab, er erscheint so weder im Terminal noch in der Prozessliste.
  # -T /usr/bin/security: das Programm darf den Wert später ohne Rückfrage lesen (für den nächtlichen Lauf).
  security add-generic-password -U -a "$KONTO" -s "$1" -l "Tagesrechnung: $2" -T /usr/bin/security -w
  echo "Gespeichert: $2."
}
echo
speichern tagesrechnung-ebay "die eBay-Cert-ID (Client Secret)"
echo
speichern tagesrechnung-smtp "das E-Mail-Passwort (bei Gmail: das App-Passwort)"

# --- 4. Konfiguration ------------------------------------------------------------------------------
echo
if [ -f config.toml ]; then
  if ./tagesrechnung.sh nummernkreis >/dev/null 2>&1; then
    echo "config.toml ist gültig."
  else
    echo "config.toml enthält einen Fehler:"
    ./tagesrechnung.sh nummernkreis || true
  fi
else
  echo "Es fehlt noch die Datei config.toml in $ORDNER"
  echo "(Vorlage: cp config.ebay.beispiel.toml config.toml)"
fi

echo
echo "Fertig. Nächster Schritt:  ./tagesrechnung.sh ebay-verbinden"
