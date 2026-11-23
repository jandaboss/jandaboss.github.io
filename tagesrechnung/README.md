# Tagesrechnung

Erstellt **am Ende jedes Tages** zu den Bestellungen des Tages eine **Rechnung als PDF** und schickt sie dem
Kunden **per E-Mail**.

```
Bestellungen (CSV-Export oder SQLite)
        │
        ▼
 je Kunde und Tag eine Sammelrechnung  ──►  PDF im Archiv  ──►  E-Mail mit PDF an den Kunden
 (oder je Bestellung eine Rechnung)         archiv/2026/RE-2026-00001.pdf
```

* **Fortlaufende, lückenlose Rechnungsnummern** (z. B. `RE-2026-00001`), auf Wunsch jedes Jahr neu beginnend
* **Keine doppelten Rechnungen**: Jede Bestellung wird genau einmal abgerechnet, auch wenn das Programm
  mehrfach läuft
* **Nichts geht verloren**: Scheitert der Versand (Server weg, Postfach voll), versucht der nächste Lauf es
  erneut, mit derselben Rechnung und derselben Nummer
* **Pflichtangaben nach § 14 UStG** auf der Rechnung, Steuer je Steuersatz (19 % / 7 %) richtig gerundet,
  Brutto- oder Nettopreise, Kleinunternehmerregelung (§ 19 UStG)
* **Probelauf**: Vorschau aller PDFs und E-Mails, ohne etwas zu versenden
* Anschriftfeld passend für Fensterumschläge (DIN 5008), optional mit eigenem Logo

## Schnellstart

Voraussetzung: Python 3.11 oder neuer.

```bash
cd tagesrechnung
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install .

# Vorschau mit den mitgelieferten Beispieldaten – es wird nichts versendet
tagesrechnung ausfuehren -c config.beispiel.toml --datum 22.11.2026 --probelauf
```

Im Ordner `probelauf/` liegen danach die Rechnungen als PDF und die E-Mails als `.eml`-Dateien
(lassen sich mit Outlook, Thunderbird oder Apple Mail öffnen).

## Einrichtung

### 1. Konfiguration anlegen

```bash
cp config.beispiel.toml config.toml
```

In `config.toml` tragen Sie Ihre Firmendaten, Bankverbindung, Steuernummer/USt-IdNr., die Quelle der
Bestellungen und den Mailserver ein. Jede Einstellung ist dort kommentiert.

### 2. Bestelldaten bereitstellen

Das Programm liest eine **CSV-Datei** (Export aus Shop, Warenwirtschaft oder Excel) oder eine
**SQLite-Datenbank**. Jede Zeile ist eine Bestellposition; Positionen derselben Bestellung haben dieselbe
Bestellnummer. Siehe `beispiel/bestellungen.csv`.

| Spalte            | Pflicht | Beispiel                     | Hinweis                                              |
|-------------------|:-------:|------------------------------|------------------------------------------------------|
| `bestellnummer`   | ✔       | `1002`                       |                                                      |
| `bestelldatum`    | ✔       | `22.11.2026 09:41`           | auch `2026-11-22T09:41:00+01:00`, Unix-Zeit          |
| `kunde_name`      | ✔       | `Erika Musterfrau`           |                                                      |
| `kunde_email`     | ✔       | `erika@example.com`          | dorthin geht die Rechnung                            |
| `artikel`         | ✔       | `Teeservice, 6-teilig`       | auch Versandkosten oder Rabatte (negativer Preis)    |
| `menge`           | ✔       | `2` oder `1,5`               |                                                      |
| `einzelpreis`     | ✔       | `49,95`                      | brutto oder netto, je nach `preise_sind`             |
| `ust_satz`        |         | `19` oder `7`                | leer = `standard_ust_satz`                           |
| `kunden_nr`       |         | `K-1001`                     | ohne Kundennummer gilt die E-Mail-Adresse als Kunde  |
| `kunde_firma`, `kunde_strasse`, `kunde_plz`, `kunde_ort`, `kunde_land`, `kunde_ust_idnr` | | | Anschrift |
| `artikelnummer`, `einheit` |  | `TS-100`, `Stk.`             |                                                      |

Heißen die Spalten in Ihrem Export anders, ordnen Sie sie unter `[quelle.spalten]` zu, z. B.
`kunde_email = "E-Mail-Adresse"`. Trennzeichen (`;` `,` Tab) und Zahlenformat (`1.234,56` oder `1,234.56`)
werden automatisch erkannt.

**SQLite statt CSV:** `typ = "sqlite"` und eine SQL-Abfrage angeben, die Spalten mit den obigen Namen
liefert (`SELECT bestell_id AS bestellnummer, …`). Platzhalter: `:von`, `:bis`, `:bis_exklusiv`.

### 3. E-Mail-Versand einrichten und testen

Das SMTP-Passwort gehört **nicht** in die Konfigurationsdatei, sondern in eine Umgebungsvariable:

```bash
export TAGESRECHNUNG_SMTP_PASSWORT='Ihr-Passwort'
tagesrechnung testmail ihre.adresse@example.com
```

Typische Einstellungen (bitte mit den Angaben Ihres Anbieters abgleichen):

| Anbieter       | `smtp_server`      | `smtp_port` | `sicherheit` | Hinweis                                           |
|----------------|--------------------|:-----------:|--------------|---------------------------------------------------|
| Gmail          | `smtp.gmail.com`   | 587         | `starttls`   | App-Passwort nötig (2-Faktor-Anmeldung aktiv)     |
| IONOS          | `smtp.ionos.de`    | 587         | `starttls`   |                                                   |
| Strato         | `smtp.strato.de`   | 465         | `ssl`        |                                                   |
| GMX            | `mail.gmx.net`     | 587         | `starttls`   | SMTP-Zugriff in den Postfach-Einstellungen freigeben |

Mit `bcc = ["buchhaltung@ihre-firma.de"]` erhalten Sie von jeder Rechnung eine Kopie.

### 4. Probelauf mit echten Daten

```bash
tagesrechnung ausfuehren --probelauf
```

Prüfen Sie die PDFs in `probelauf/`. Im Probelauf wird nichts versendet, und das Rechnungsbuch bleibt
unverändert. Die Nummern im Probelauf sind die, die der echte Lauf vergeben würde.

### 5. Jeden Abend automatisch

Empfohlen: **täglich um 23:55 Uhr** mit `--nachholen 2`. Bestellungen, die nach dem Lauf noch
eingehen (23:55–23:59 Uhr), und Tage, an denen der Rechner aus war, werden beim nächsten Lauf automatisch
mit abgerechnet. Bereits abgerechnete Bestellungen werden dabei übersprungen.

| System  | Datei                                                         |
|---------|---------------------------------------------------------------|
| Linux (systemd) | `deploy/tagesrechnung.service` + `deploy/tagesrechnung.timer` |
| Linux/macOS (cron) | `deploy/crontab.beispiel`                           |
| Windows | `deploy/windows-aufgabe.ps1` (Aufgabenplanung)                |

Wichtig: Das Programm muss auf einem Rechner oder Server laufen, der um diese Zeit eingeschaltet ist und
die Bestelldaten erreicht. GitHub Pages (diese Website) kann keine Programme ausführen.

Am **Rückgabewert** erkennt die Zeitsteuerung, ob alles geklappt hat: `0` = alles versendet,
`1` = Versand- oder Datenfehler (Details im Log), `2` = Konfigurations- oder Bedienfehler.

## Befehle

```bash
tagesrechnung ausfuehren                    # Bestellungen von heute abrechnen und versenden
tagesrechnung ausfuehren --nachholen 2      # … plus noch nicht abgerechnete der 2 Vortage
tagesrechnung ausfuehren --datum gestern    # bestimmter Tag: gestern, 2026-11-22, 22.11.2026
tagesrechnung ausfuehren --probelauf        # Vorschau, nichts wird versendet oder festgeschrieben
tagesrechnung liste                         # alle Rechnungen mit Versandstatus
tagesrechnung liste --offen                 # noch nicht zugestellte Rechnungen
tagesrechnung testmail ich@example.com      # SMTP-Einstellungen prüfen
```

Alle Befehle akzeptieren `-c pfad/zur/config.toml`, `--logdatei datei.log` und `-v`.

## Was passiert bei Fehlern?

| Situation                                   | Verhalten                                                                  |
|---------------------------------------------|----------------------------------------------------------------------------|
| Mailserver nicht erreichbar, Passwort falsch | Rechnung bleibt „offen“, der nächste Lauf versendet sie erneut (gleiche Nummer) |
| Fehlerhafte Zeile in den Bestelldaten       | Diese **eine Bestellung** wird übersprungen und gemeldet, alle anderen werden abgerechnet; nach der Korrektur rechnet der nächste Lauf sie ab (bei älteren Bestellungen mit `--datum`) |
| PDF kann nicht erzeugt werden               | Die Rechnungsnummer wird nicht verbraucht (keine Lücke im Nummernkreis)     |
| Programm läuft zweimal                      | Bereits abgerechnete Bestellungen werden übersprungen                       |
| Nummernkreis falsch umgestellt              | Bereits vergebene Nummern werden nicht erneut benutzt, archivierte PDFs nie überschrieben |

Das Rechnungsbuch `rechnungsbuch.sqlite` ist das Gedächtnis des Programms: Es enthält den Nummernkreis
und vermerkt, welche Bestellung auf welcher Rechnung steht. **Sichern Sie es zusammen mit dem Ordner
`archiv/`** und löschen Sie es nicht.

## Rechtliche Hinweise

* Die Rechnungen enthalten die Pflichtangaben nach § 14 Abs. 4 UStG, sofern Firmen- und Kundendaten
  vollständig gepflegt sind. Rechnungen und Rechnungsbuch unterliegen den gesetzlichen
  Aufbewahrungsfristen (GoBD).
* **E-Rechnung im B2B-Bereich:** Für Rechnungen an Unternehmen in Deutschland wird die strukturierte
  E-Rechnung (XRechnung/ZUGFeRD) schrittweise Pflicht: ab 2027 für Unternehmen mit mehr als 800.000 €
  Vorjahresumsatz, ab 2028 für alle. Das Programm erzeugt PDF-Rechnungen. Für Privatkunden (B2C) genügt
  das weiterhin. Wenn Sie überwiegend an Firmen verkaufen, lässt sich ZUGFeRD ergänzen.
* Sonderfälle wie innergemeinschaftliche Lieferungen (Reverse Charge), steuerfreie Umsätze oder
  Stornorechnungen deckt das Programm nicht ab.
* Das ist keine Steuerberatung. Lassen Sie das Rechnungslayout im Zweifel einmal von Ihrer
  Steuerberatung prüfen.

## Entwicklung

```bash
pip install -e ".[test]"
pytest
```

Aufbau: `quellen.py` (Bestelldaten lesen) → `berechnung.py` (Bestellungen gruppieren, Summen) →
`pdf.py` (Rechnung) → `versand.py` (E-Mail) · `rechnungsbuch.py` (Nummernkreis, Status) ·
`ablauf.py` (Tageslauf) · `cli.py` (Kommandozeile).
