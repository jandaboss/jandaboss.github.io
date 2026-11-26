#!/bin/bash
# Täglicher Lauf um 17:00 Uhr über die macOS-Zeitsteuerung (launchd).
#
#   bash deploy/macos/zeitplan.sh an       einschalten
#   bash deploy/macos/zeitplan.sh aus      ausschalten
#   bash deploy/macos/zeitplan.sh status   anzeigen
#
# Andere Uhrzeit: TAGESRECHNUNG_STUNDE=23 TAGESRECHNUNG_MINUTE=55 bash deploy/macos/zeitplan.sh an
#
# Schläft der Mac zur Startzeit, startet der Lauf beim Aufwachen. Ist er ausgeschaltet, holt der
# nächste Lauf die Bestellungen nach: alle noch nicht abgerechneten der letzten 10 Tage
# (TAGESRECHNUNG_NACHHOLEN). Mit erst_nach_versand = true sind das auch Bestellungen, die erst
# Tage nach dem Kauf versendet wurden. Ausgaben stehen in tagesrechnung.log.
set -e

ORDNER="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="de.tagesrechnung.taeglich"
PLIST="$HOME/Library/LaunchAgents/$NAME.plist"
ZIEL="gui/$(id -u)"
STUNDE="${TAGESRECHNUNG_STUNDE:-17}"
MINUTE="${TAGESRECHNUNG_MINUTE:-0}"
NACHHOLEN="${TAGESRECHNUNG_NACHHOLEN:-10}"
for zahl in "$STUNDE" "$MINUTE" "$NACHHOLEN"; do
  case "$zahl" in
    ''|*[!0-9]*) echo "Stunde, Minute und Nachholtage müssen ganze Zahlen sein (erhalten: '$zahl')."; exit 2 ;;
  esac
done

xml() {  # Sonderzeichen für die plist-Datei maskieren
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

case "${1:-status}" in
  an)
    if [ ! -x "$ORDNER/.venv/bin/tagesrechnung" ]; then
      echo "Bitte zuerst einrichten: bash deploy/macos/einrichten.sh"
      exit 1
    fi
    if [ ! -f "$ORDNER/config.toml" ]; then
      echo "Es fehlt die Datei config.toml in $ORDNER"
      exit 1
    fi
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$NAME</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$(xml "$ORDNER/tagesrechnung.sh")</string>
    <string>ausfuehren</string>
    <string>--nachholen</string>
    <string>$NACHHOLEN</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$STUNDE</integer>
    <key>Minute</key>
    <integer>$MINUTE</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$(xml "$ORDNER/tagesrechnung.log")</string>
  <key>StandardErrorPath</key>
  <string>$(xml "$ORDNER/tagesrechnung.log")</string>
</dict>
</plist>
EOF
    plutil -lint "$PLIST" >/dev/null
    launchctl bootout "$ZIEL/$NAME" 2>/dev/null || true
    launchctl bootstrap "$ZIEL" "$PLIST"
    printf "Eingeschaltet: Tagesrechnung läuft täglich um %02d:%02d Uhr.\n" "$STUNDE" "$MINUTE"
    echo "Protokoll: $ORDNER/tagesrechnung.log"
    ;;
  aus)
    launchctl bootout "$ZIEL/$NAME" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Ausgeschaltet: kein täglicher Lauf mehr."
    ;;
  status)
    if launchctl print "$ZIEL/$NAME" >/dev/null 2>&1; then
      echo "Eingeschaltet ($PLIST)."
    else
      echo "Ausgeschaltet."
    fi
    ;;
  *)
    echo "Aufruf: bash deploy/macos/zeitplan.sh an|aus|status"
    exit 2
    ;;
esac
