# Label Converter

Chrome-Erweiterung, die A4-Versandlabels (z. B. für einen Maxibrief) in das Format eines Etikettendruckers umwandelt, etwa 100 × 150 mm (4 × 6″).

- Findet das Etikett auf der A4-Seite automatisch, ein eigener Ausschnitt lässt sich per Ziehen festlegen und für gleich aufgebaute Labels merken.
- Dreht das Etikett passend und lässt es in Originalgröße; verkleinert wird nur, wenn es sonst nicht passt.
- Übernimmt das Etikett als Vektorgrafik, Barcodes bleiben scharf. Geschützte PDFs werden automatisch als Bild mit 300 dpi eingefügt.
- Mehrere Dateien oder Seiten ergeben ein PDF mit einem Etikett pro Seite. Leere Seiten werden übersprungen.
- Läuft komplett lokal. Einzige Berechtigung: `storage` für die Einstellungen.

## Dateien

| Pfad | Inhalt |
| --- | --- |
| `index.html` | Download-Seite für Dritte (GitHub Pages: `/label-converter/`) |
| `label-converter.zip` | Fertiges Paket zum Weitergeben, wird mit `build.sh` erzeugt |
| `extension/` | Quellcode der Erweiterung (Manifest V3) |
| `extension/converter.*` | Die Konverter-Seite. Läuft auch ohne Installation direkt im Browser |
| `extension/lib/` | pdf.js 4.10.38 (Apache 2.0) und pdf-lib 1.17.1 (MIT), unverändert |

## Neue Version veröffentlichen

1. Änderungen in `extension/` machen und `version` in `extension/manifest.json` erhöhen.
2. `./build.sh` ausführen, das erneuert `label-converter.zip`.
3. Versionsnummer und ZIP-Größe in `index.html` anpassen.

## Installation (für Nutzer)

ZIP entpacken, `chrome://extensions` öffnen, „Entwicklermodus“ einschalten, „Entpackte Erweiterung laden“ und den Ordner `label-converter` wählen. Die ausführliche Anleitung steht auf der Download-Seite.

## Chrome Web Store (optional)

Ohne Entwicklermodus und mit automatischen Updates geht es über den Chrome Web Store. Dafür braucht es ein Entwicklerkonto (einmalige Gebühr) und dann den Upload von `label-converter.zip`. Angaben, die der Store abfragt:

- **Einzelzweck:** Wandelt A4-Versandlabels in PDFs im Format von Etikettendruckern um.
- **Berechtigung `storage`:** Speichert die gewählten Einstellungen (Format, Drehung, gemerkter Ausschnitt) lokal.
- **Datennutzung:** Es werden keine Daten erhoben oder übertragen; alle Dateien werden lokal im Browser verarbeitet.
- **Remote-Code:** Keiner, alle Bibliotheken liegen im Paket.
