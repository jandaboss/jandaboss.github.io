# Tagesrechnung

Erstellt **am Ende jedes Tages** zu den Bestellungen des Tages eine **Rechnung als PDF** und schickt sie dem
Kunden **per E-Mail**. Die Bestellungen kommen **direkt von eBay** oder aus einer CSV-Datei bzw. SQLite-Datenbank.

```
Bestellungen (eBay, CSV-Export oder SQLite)
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

## eBay-Anbindung einrichten

Das Programm holt die Bestellungen über die offizielle eBay-Schnittstelle (Sell Fulfillment API) und
darf dabei **nur lesen**. Die Einrichtung ist einmalig.

### 1. eBay-Entwicklerkonto und Schlüssel

1. Auf [developer.ebay.com](https://developer.ebay.com) kostenlos für das *eBay Developers Program*
   registrieren. Die Freischaltung des Kontos kann etwas dauern.
2. Unter **Application Keys** einen Schlüsselsatz für **Production** anlegen. Sie erhalten
   **App ID (Client ID)** und **Cert ID (Client Secret)**.
3. **Marketplace Account Deletion:** eBay schaltet Produktionsschlüssel erst frei, wenn Sie Meldungen über
   gelöschte eBay-Konten empfangen oder eine Ausnahme beantragen. Die Ausnahme gibt es nur, wenn man
   *keine* eBay-Daten speichert oder für eBay selbst arbeitet. Beides trifft hier nicht zu, denn Rechnungen
   enthalten Käuferdaten und müssen aufbewahrt werden. Deshalb: Empfangsstelle einrichten, siehe
   [eBay-Meldungen über gelöschte Konten](#ebay-meldungen-über-gelöschte-konten).
4. Neben dem Production-Schlüssel auf **User Tokens** → **Get a Token from eBay via Your Application** →
   **Add eBay Redirect URL**. Dort eintragen:
   * *Your auth accepted URL*: eine beliebige eigene `https://`-Seite, z. B. Ihre Website. Dorthin schickt
     eBay den Browser nach der Freigabe, mit dem Code in der Adresse.
   * *Privacy policy URL*: die Datenschutzerklärung Ihrer Website.

   eBay zeigt danach den **RuName** an (eine Zeichenkette wie `Ihr_Name-IhrName-Tagesr-abcdefghi`).

### eBay-Meldungen über gelöschte Konten

eBay schickt allen Programmen mit API-Zugang Meldungen, wenn ein eBay-Konto gelöscht wird (laut eBay bis
zu ca. 1.500 am Tag). Die Vorlage `deploy/ebay-kontoloeschung-worker.js` nimmt sie entgegen. Sie läuft
kostenlos bei Cloudflare Workers (100.000 Anfragen am Tag frei) und braucht keinen eigenen Server.

1. Einen Token ausdenken oder erzeugen: 32–80 Zeichen, nur Buchstaben, Ziffern, `_` und `-`.
   Ihn in der Vorlage bei `VERIFICATION_TOKEN` eintragen.
2. Auf [dash.cloudflare.com](https://dash.cloudflare.com) kostenlos registrieren. Unter **Workers & Pages**
   einen neuen Worker anlegen („Hello World“-Vorlage), z. B. mit dem Namen `meinshop-ebay`, und **Deploy**
   klicken.
3. **Edit code**: den gesamten Code durch die Vorlage ersetzen und erneut **Deploy** klicken.
4. Im Browser `https://meinshop-ebay.<ihr-name>.workers.dev/ebay` öffnen. Es muss „eBay-Empfangsstelle ist
   bereit.“ erscheinen.
5. Im eBay-Entwicklerportal unter **Alerts & Notifications → Marketplace Account Deletion**:
   * Schalter *Exempted* **aus**.
   * E-Mail für Störungsmeldungen eintragen.
   * Endpoint: die Adresse aus Schritt 4, **mit** `/ebay` am Ende.
   * Verification token: derselbe Token wie in Schritt 1.
   * **Save**. eBay prüft die Adresse sofort. Danach **Send Test Notification**.

Der Worker bestätigt jede Meldung. Rechnungen werden nicht gelöscht, denn sie unterliegen der gesetzlichen
Aufbewahrungspflicht. Weitere Käuferdaten speichert das Programm nicht.

### 2. Konfiguration

```bash
cp config.ebay.beispiel.toml config.toml
```

`client_id` und `ru_name` eintragen, ebenso Ihre Firmendaten, den Mailserver und `abrechnen_ab`
(ab diesem Tag werden Rechnungen verschickt, ältere Bestellungen nie). Die Cert ID gehört in eine
Umgebungsvariable:

```bash
export TAGESRECHNUNG_EBAY_SECRET='PRD-…'
export TAGESRECHNUNG_SMTP_PASSWORT='…'
```

### 3. Mit eBay verbinden

```bash
tagesrechnung ebay-verbinden
```

Das Programm zeigt eine Adresse an. Öffnen Sie sie im Browser, melden Sie sich mit Ihrem
eBay-Verkäuferkonto an und stimmen Sie zu. Danach landen Sie auf Ihrer *auth accepted URL*. Kopieren Sie
die komplette Adresse aus der Adresszeile (sie enthält `code=`) und fügen Sie sie im Programm ein. Der Code
ist nur wenige Minuten gültig.

Die Freigabe gilt laut eBay etwa **18 Monate** und liegt in `ebay-zugang.json` (vertraulich, nicht
weitergeben). 30 Tage vor Ablauf warnt das Programm bei jedem Lauf. Dann einfach `ebay-verbinden`
wiederholen.

### 4. Prüfen und Probelauf

```bash
tagesrechnung ebay-pruefen --tage 14       # zeigt die Bestellungen und was mit ihnen passieren würde
tagesrechnung ausfuehren --nachholen 7 --probelauf
```

### Was mit eBay-Bestellungen passiert

| Bestellung                                   | Verhalten                                                        |
|----------------------------------------------|------------------------------------------------------------------|
| bezahlt                                      | Rechnung wird erstellt und verschickt                            |
| noch nicht bezahlt (z. B. Auktion)           | wartet; wird abgerechnet, sobald bezahlt (mit `--nachholen 7`)   |
| storniert, Storno beantragt oder voll erstattet | keine Rechnung                                                |
| teilweise erstattet                          | wird gemeldet – bitte von Hand abrechnen                         |
| Summe passt nicht zum eBay-Gesamtbetrag      | wird gemeldet statt mit falschem Betrag abgerechnet              |

* **E-Mail-Adresse:** eBay gibt statt der echten Adresse eine verschlüsselte Adresse
  (`…@members.ebay.com`) heraus und leitet die Mail samt PDF an den Käufer weiter. eBay liefert diese
  Adresse nur etwa 14 Tage nach dem Kauf, für den täglichen Lauf reicht das. Kann eBay eine Mail nicht
  zustellen, schickt eBay eine Nachricht an die Absenderadresse. Behalten Sie deshalb `bcc` auf Ihre
  eigene Adresse und schauen Sie in den ersten Tagen in Ihr Postfach.
* **Versandkosten, Rabatte, Versandrabatte und Preisanpassungen** werden als eigene Positionen
  aufgeführt. Bei gemischten Steuersätzen (`[ebay.ust_saetze]`) werden die Versandkosten anteilig
  aufgeteilt.
* **Zahlungshinweis:** Die eBay-Vorlage schreibt „Die Zahlung wird über eBay abgewickelt“ statt „bereits
  bezahlt“, weil Käufer mit Klarna-Rechnung oder Ratenkauf den Betrag zu diesem Zeitpunkt noch Klarna schulden.
* **Rechnungsanschrift** ist die beim eBay-Konto des Käufers hinterlegte Adresse, ersatzweise die
  Lieferanschrift. Wünscht der Käufer eine andere Anschrift, siehe unten.
* **Rücksendungen nach dem Rechnungsversand** erzeugen keine automatische Gutschrift. Diese bitte von Hand
  ausstellen.

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

Empfohlen: **täglich um 23:55 Uhr** mit `--nachholen 7`. Bestellungen, die nach dem Lauf noch
eingehen (23:55–23:59 Uhr), die erst später bezahlt werden, und Tage, an denen der Rechner aus war, werden
beim nächsten Lauf automatisch mit abgerechnet. Bereits abgerechnete Bestellungen werden dabei
übersprungen.

| System  | Datei                                                         |
|---------|---------------------------------------------------------------|
| Linux (systemd) | `deploy/tagesrechnung.service` + `deploy/tagesrechnung.timer` |
| Linux/macOS (cron) | `deploy/crontab.beispiel`                           |
| Windows | `deploy/windows-aufgabe.ps1` (Aufgabenplanung)                |

Wichtig: Das Programm muss auf einem Rechner oder Server laufen, der um diese Zeit eingeschaltet ist und
die Bestelldaten erreicht. GitHub Pages (diese Website) kann keine Programme ausführen.

Am **Rückgabewert** erkennt die Zeitsteuerung, ob alles geklappt hat: `0` = alles versendet,
`1` = Versand- oder Datenfehler (Details im Log), `2` = Konfigurations- oder Bedienfehler.

## Käuferhinweise und abweichende Rechnungsanschrift

Schreibt ein eBay-Käufer beim Kauf etwas wie *„Bitte stellen Sie die Rechnung auf die Fa. … GmbH aus“*,
erkennt das Programm das an Wörtern wie „Rechnung“, „Firma“, „GmbH“ oder „USt-IdNr.“. Es hält die
Bestellung dann an, statt die Rechnung an die falsche Anschrift zu schicken, und meldet sie bei jedem Lauf.
Auch `ebay-pruefen` zeigt den Hinweis an. Andere Hinweise („bitte als Geschenk verpacken“) halten nichts
auf.

Gewünschte Anschrift eintragen. Bei einer Firma steht nur die Firma auf der Rechnung, die Rechnung beginnt
mit „Sehr geehrte Damen und Herren“. Mit `--name "…"` lässt sich ein Ansprechpartner ergänzen. Die E-Mail
geht weiterhin über eBay an den Käufer und spricht ihn mit Namen an.

```bash
tagesrechnung anschrift 12-34567-89012 --firma "Beispiel GmbH" --strasse "Musterstr. 1" --plz 70191 --ort Stuttgart
```

Oder die Bestellung ohne Änderung freigeben:

```bash
tagesrechnung anschrift 12-34567-89012 --wie-bestellt
```

Der nächste Lauf erstellt dann die Rechnung. Die Bestellung bekommt eine eigene Rechnung, auch wenn derselbe
Käufer am selben Tag noch mehr gekauft hat. Die Einträge stehen in `rechnungsanschriften.toml` und
funktionieren genauso für CSV- und SQLite-Bestellungen. Ist die Bestellung schon abgerechnet, warnt der
Befehl. Die bestehende Rechnung muss dann von Hand korrigiert werden.

Angehaltene Bestellungen holt der tägliche Lauf mit `--nachholen 7` noch bis zu 7 Tage lang nach. Danach
bitte mit `--datum` gezielt abrechnen.

## Rechnungsnummern

Das Programm vergibt die Nummern lückenlos und fortlaufend, standardmäßig mit dem Jahr davor
(`nummer_format = "{jahr}-{nummer}"` → 2026-1, 2026-2, …). Am 1. Januar beginnt die Zählung wieder bei 1.

**Umstieg von bisherigen Rechnungen** (z. B. eBays Rechnungsnummern bis 2026-250): einmalig festlegen, an
welche Nummer das Programm anschließt, und die bisherige Nummerierung abschalten. Sonst bekämen Bestellungen
zwei verschiedene Rechnungsnummern.

```bash
tagesrechnung nummernkreis --letzte 250    # nächste Rechnung: 2026-251, im Jahr 2027 wieder ab 2027-1
tagesrechnung nummernkreis                 # aktuellen Stand anzeigen
```

## Befehle

```bash
tagesrechnung ausfuehren                    # Bestellungen von heute abrechnen und versenden
tagesrechnung ausfuehren --nachholen 7      # … plus noch nicht abgerechnete der 7 Vortage
tagesrechnung ausfuehren --datum gestern    # bestimmter Tag: gestern, 2026-11-22, 22.11.2026
tagesrechnung ausfuehren --probelauf        # Vorschau, nichts wird versendet oder festgeschrieben
tagesrechnung liste                         # alle Rechnungen mit Versandstatus
tagesrechnung liste --offen                 # noch nicht zugestellte Rechnungen
tagesrechnung testmail ich@example.com      # SMTP-Einstellungen prüfen
tagesrechnung ebay-verbinden                # einmalig: Programm mit dem eBay-Konto verbinden
tagesrechnung ebay-pruefen --tage 14        # eBay-Bestellungen ansehen, ohne abzurechnen
tagesrechnung anschrift NR --firma … --strasse … --plz … --ort …   # abweichende Rechnungsanschrift
tagesrechnung nummernkreis --letzte 250     # an bisherige Rechnungsnummern anschließen
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
* **Einzelunternehmen mit Shopnamen:** Auf die Rechnung gehört Ihr vollständiger Name. Dafür
  `inhaber = "Vorname Nachname"` angeben, er erscheint in Absenderzeile und Fußzeile.
* **Kleinunternehmer (§ 19 UStG)** setzen `kleinunternehmer = true`. Sie sind außerdem dauerhaft von der
  Pflicht befreit, E-Rechnungen auszustellen.
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

Aufbau: `quellen.py` (Bestelldaten lesen, `ebay.py` für eBay) → `berechnung.py` (Bestellungen gruppieren, Summen) →
`pdf.py` (Rechnung) → `versand.py` (E-Mail) · `rechnungsbuch.py` (Nummernkreis, Status) ·
`anschriften.py` (abweichende Rechnungsanschriften) · `ablauf.py` (Tageslauf) · `cli.py` (Kommandozeile).
