# Validation Watchdog v0.7.4

## Ablauf

1. Eine bereinigte, dauerhafte Corpus-Kopie wird erzeugt oder bei identischem Fingerprint wiederverwendet.
2. Daraus wird ein separater, kurzlebiger Validierungsworkspace erstellt.
3. Godot läuft mit `--headless --xr-mode off --disable-crash-handler --path <workspace> --import`.
4. Ausgaben erscheinen sofort in der technischen Logansicht.
5. Die einfache Ansicht erhält alle fünf Sekunden einen Status-Heartbeat.
6. Bei Ablauf des Zeitlimits werden Godot und seine gestarteten Unterprozesse beendet.
7. Ein einmaliger zweiter Lauf darf vorhandene Importcaches der isolierten Kopie nutzen.
8. Ergebnis und vollständiges technisches Log werden gespeichert.
9. Der Validierungsworkspace wird entfernt; eine verbleibende Sperre wird protokolliert, blockiert aber die Corpus-Kopie nicht.

## Verhalten bei Fehlern

- Timeout: Projekt wird nicht trainingsaktiviert, der Importjob wird jedoch geordnet abgeschlossen und zeigt den letzten erfolgreichen Schritt.
- Parserfehler: Projekt bleibt quarantänisiert; Parserausgabe bleibt im technischen Log und Report.
- Studioabsturz: Beim nächsten Import wird die gespeicherte PID nur nach Befehlszeilenprüfung beendet.
- Alter v0.7.3-Prozess: `RECOVER_STUCK_VALIDATION.bat` verwenden.

## Datenschutz und Datenerhalt

Die Originalprojekte werden weiterhin nur gelesen. Die Godot-Prüfung läuft weder im Originalordner noch in der dauerhaften Corpus-Kopie. Private Projekte werden nicht hochgeladen und nicht als weiterverteilbar markiert.
