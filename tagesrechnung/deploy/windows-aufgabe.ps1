# Richtet unter Windows eine tägliche Aufgabe um 23:55 Uhr ein.
# Ausführen in PowerShell im Programmordner:   powershell -ExecutionPolicy Bypass -File deploy\windows-aufgabe.ps1
# Vorher einmalig die Passwörter hinterlegen:
#   setx TAGESRECHNUNG_SMTP_PASSWORT "geheim"
#   setx TAGESRECHNUNG_EBAY_SECRET "PRD-..."

$Ordner = (Resolve-Path "$PSScriptRoot\..").Path
$Programm = Join-Path $Ordner ".venv\Scripts\tagesrechnung.exe"
$Argumente = "ausfuehren -c `"$Ordner\config.toml`" --nachholen 7 --logdatei `"$Ordner\tagesrechnung.log`""

$Aktion = New-ScheduledTaskAction -Execute $Programm -Argument $Argumente -WorkingDirectory $Ordner
$Ausloeser = New-ScheduledTaskTrigger -Daily -At "23:55"
# StartWhenAvailable: verpasste Läufe (Rechner aus) beim nächsten Start nachholen
$Einstellungen = New-ScheduledTaskSettingsSet -StartWhenAvailable

Register-ScheduledTask -TaskName "Tagesrechnung" -Action $Aktion -Trigger $Ausloeser -Settings $Einstellungen `
    -Description "Rechnungen zu den Bestellungen des Tages erstellen und per E-Mail versenden" -Force
Write-Host "Aufgabe 'Tagesrechnung' eingerichtet (täglich 23:55 Uhr)."
