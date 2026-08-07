# Godot Coder Studio v0.10.1

Das Studio läuft weiterhin auf `127.0.0.1` und benötigt keine Cloud-API. Optional stellt Tailscale Serve einen privaten HTTPS-Zugang innerhalb des eigenen Tailnets bereit.

## Bereiche

- **Chat & Code:** Checkpoint laden, Completion erzeugen, mit Godot prüfen und als Lernbeispiel speichern.
- **Training:** Konfiguration wählen, von Zufall oder kompatiblem Checkpoint starten, Live-Logs und Stop.
- **Wissensaufbau:** fünf geführte Schritte vom Git-Repository bis zum Tokenstream.
- **Data Lab:** GDScript lesen/bearbeiten; bestehende Dateien werden vorher gesichert.
- **Modelle:** `best`, `latest` und aufbewahrte Step-Checkpoints auswählen.
- **System:** Python, PyTorch, CUDA, GPU, Godot und Projektpfad prüfen.

## Anfänger-Schutz

Unbekannte Lizenzen, ungültige Git-URLs/Refs, Pfade außerhalb des Projekts, unpassende Tokenizer und ungültige Trainingskonfigurationen werden vor dem Start abgewiesen. Große Jobs laufen separat und können gestoppt werden.

## Professional Training Core

Die Trainingsansicht enthält eine Preflight-Ampel. Rot blockiert den Nachtlauf, Gelb erlaubt eine bewusste Prüfung und Grün bedeutet, dass Pflichtprüfungen bestanden sind. Erweiterte Optionen bleiben eingeklappt.


## Secure Remote Studio

Der Remote-Bereich zeigt Tailscale-Status, Identität, Lesemodus, PIN-Sitzung, private Linkdownloads, ZIP-Uploads und den lokalen Importordner. Alle Jobs laufen auf dem PC. API-Daten werden nicht durch die PWA offline gecacht. Eine öffentliche Tailscale-Funnel-Freigabe ist nicht vorgesehen.


## Parser-Fallback

Bei einem festgefahrenen Godot-Mono-Editorimport wechselt das Studio automatisch auf die dateiweise `--check-only`-Prüfung. Der aktuelle Pfad, Dateizähler und ausgeschlossene Parserfehler werden im Live-Fortschritt angezeigt.
