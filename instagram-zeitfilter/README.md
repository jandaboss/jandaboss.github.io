# Instagram Zeitfilter (Chrome-Extension)

Zeigt die Beiträge eines Instagram-Profils **chronologisch – älteste zuerst** – und
filtert sie nach einem **Zeitraum** (von Tag X bis Tag Y).

## Funktionen

- 📅 Runder Button unten rechts auf instagram.com öffnet den Zeitfilter –
  alternativ ein Klick auf das Extension-Symbol in der Chrome-Leiste (Puzzle-Symbol → Zeitfilter anheften)
- **Von / Bis** mit Kalender-Auswahl, Schnellwahl (letzte 30 Tage, dieses/letztes Jahr, alles)
- **Sortierung:** älteste oder neueste zuerst
- **Typ-Filter:** Fotos, Alben, Videos, Reels
- **Kalender-Übersicht:** Jahre × Monate mit Anzahl der Beiträge – ein Klick auf einen
  Monat oder ein Jahr setzt den Zeitraum
- Klick auf ein Bild öffnet den Beitrag in einem neuen Tab
- Lädt nur so weit zurück wie nötig (bei „ab 01.03.2023“ wird nicht das ganze Profil geladen)

## Installation

1. Diesen Ordner `instagram-zeitfilter` herunterladen (bzw. die ZIP entpacken).
2. In Chrome `chrome://extensions` öffnen.
3. Oben rechts den **Entwicklermodus** einschalten.
4. **„Entpackte Erweiterung laden“** klicken und den Ordner `instagram-zeitfilter` auswählen.
5. instagram.com öffnen (angemeldet), ein Profil aufrufen und auf 📅 klicken.

## Wenn es nicht funktioniert

1. **Richtigen Ordner geladen?** Beim Laden muss der Ordner gewählt werden, in dem direkt
   `manifest.json` liegt (nach dem Entpacken evtl. `instagram-zeitfilter/instagram-zeitfilter`).
2. **Nach Installation/Update:** unter `chrome://extensions` auf ⟳ (Neu laden) klicken und
   den Instagram-Tab neu laden (F5).
3. **Kein Button sichtbar?** Auf das Extension-Symbol in der Chrome-Leiste klicken.
4. **Fehlermeldung im Fenster?** Den roten Text notieren. Mehr Details: `F12` → Reiter
   „Console“ → nach `[Zeitfilter]` filtern.

## Hinweise

- Die Extension nutzt die interne Web-API von Instagram mit deiner bestehenden Anmeldung.
  Es werden keine Daten an Dritte gesendet. Private Profile funktionieren nur, wenn du ihnen folgst.
- Bei Profilen mit sehr vielen Beiträgen dauert „alles laden“ eine Weile (ca. 33 Beiträge pro
  Anfrage, mit kurzer Pause gegen Rate-Limits). Mit **Stopp** kann man abbrechen,
  „Anzeigen“ setzt dann fort.
- Instagram ändert seine interne API gelegentlich – dann muss die Extension ggf. angepasst werden.
- Nutzung auf eigene Verantwortung (Instagram-Nutzungsbedingungen beachten).
