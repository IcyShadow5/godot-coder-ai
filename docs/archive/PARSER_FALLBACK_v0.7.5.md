# Parser Fallback v0.7.5

## Diagnose aus dem Pennyshire-Log

Die statische Prüfung hatte 85 GDScript-Dateien vollständig verarbeitet: 83 ohne Hinweise und 2 mit Hinweisen. Erst danach hing Godot beim vollständigen Mono-Editorimport.

Die entscheidenden Meldungen waren:

- `Plugin is not attached to debugger.`
- `Unable to start the timer because it's not inside the scene tree.`
- `GodotTools.HotReloadAssemblyWatcher.RestartTimer()`
- `EditorSettings not instantiated yet when getting setting "export/android/shutdown_adb_on_exit".`

Danach entstand keine neue technische Ausgabe mehr. Das war kein produktiver Parserlauf.

## Verhalten ab v0.7.5

1. Vollständiger Godot-Import startet in einer isolierten Arbeitskopie.
2. Bekannte Mono-/Editor-Infrastrukturfehler lösen sofort einen kontrollierten Abbruch aus.
3. Bei sonstiger Stille greift ein Inaktivitäts-Watchdog.
4. Godot prüft anschließend jedes trainierbare `.gd`-Skript mit `--check-only`.
5. Das Studio zeigt den aktuellen Dateipfad und `Datei x/y`.
6. Fehlgeschlagene Dateien werden einzeln ausgeschlossen.
7. Besteht mindestens eine trainierbare Datei und liegen keine Secrets vor, bleibt das Projekt für das Training aktiviert.

## Konfiguration

Standardwerte:

```text
GODOT_CODER_VALIDATION_TIMEOUT_SECONDS=420
GODOT_CODER_VALIDATION_IDLE_TIMEOUT_SECONDS=45
GODOT_CODER_PARSER_FILE_TIMEOUT_SECONDS=20
```

Die Werte können als Umgebungsvariablen verändert werden. Eine Erhöhung sollte nur erfolgen, wenn technische Logs weiterhin echten Fortschritt zeigen.
