# Godot Coder Studio v0.10.2

Das Studio läuft weiterhin lokal auf `127.0.0.1` und benötigt keine Cloud-API. Optional stellt Tailscale Serve einen privaten HTTPS-Zugang innerhalb des eigenen Tailnets bereit.

## Bereiche

- **Chat & Code:** Checkpoint laden, Completion erzeugen, mit Godot prüfen und als Lernbeispiel speichern.
- **Training:** Konfiguration wählen, von Zufall oder kompatiblem Checkpoint starten, Live-Logs und Stop.
- **Wissensaufbau:** fünf geführte Schritte vom Git-Repository bis zum Tokenstream.
- **Data Lab:** GDScript lesen/bearbeiten; bestehende Dateien werden vorher gesichert.
- **Modelle:** `best`, `latest` und aufbewahrte Step-Checkpoints auswählen.
- **System:** Python, PyTorch, CUDA, GPU, Godot und Projektpfad prüfen.

## Chat & Code

Checkpoint wählen, Prompt eingeben und eine GDScript-Completion erzeugen. Die Ausgabe kann direkt mit Godot geprüft werden; bestätigte Beispiele lassen sich als Lernbeispiel speichern. Beim Speichern wird die Ziel-Datei vorher gesichert.

## Data Lab

Das Data Lab ist ein kleiner Editor für den lokalen Korpus: GDScript-Dateien lesen und bearbeiten, vorhandene Einträge löschen und neue Dateien anlegen. Die Liste lässt sich per Datentyp filtern (alle Daten, aktive Trainingstokens, eigene Rohdateien, Aufgabendaten oder Train-/Validation-/Test-Split) und zusätzlich über das Suchfeld durchsuchen. Jede Änderung an einer bestehenden Datei legt vorher eine Sicherung an — nichts wird blind überschrieben.

## Wissensaufbau: die geführten Schritte

1. **Quellen auswählen** — die offiziellen Godot-Quellen sind vorausgewählt; deaktivierbar, plus Erweiterungs-Pakete (klein / Großausbau · 5M / Maximalausbau · 20M). Eigene Git-Quellen lassen sich nur mit erlaubter Lizenz hinzufügen.
2. **Quellen laden & bereinigen** — `fetch` lädt die ausgewählten Repositories, `build` erzeugt das Staging-Manifest (Lizenzen, Splits, Deduplikation).
3. **Prüfen** — `validate` lässt Godot den Code durch den Parser laufen; danach folgt das Corpus-Audit mit Token-Zählung.
4. **Tokenstrom** — `train-bpe` baut den Byte-BPE-Tokenizer und den versionierten Tokenstrom.
5. **Dataset vorbereiten** — das finale Trainings-Dataset mit Shards und Hashes; danach kann Training starten.

Für den privaten Import eigener Projekte gelten die Optionen im Abschnitt „Import-Optionen für große Mengen“.

## Secure Remote Studio

Einrichtung in vier Schritten:

1. `python -m godot_coder.remote_access configure` ausführen (richtet Tailscale Serve ein; das Studio bleibt auf `127.0.0.1:8765` gebunden).
2. Studio mit `python -m godot_coder.studio` starten.
3. Die angezeigte Tailscale-HTTPS-Adresse am Handy/Tablet öffnen.
4. Wieder entfernen mit `python -m godot_coder.remote_access disable`.

Der Remote-Bereich zeigt Tailscale-Status, Identität, Lesemodus, PIN-Sitzung, private Linkdownloads, ZIP-Uploads und den lokalen Importordner. Alle Jobs laufen auf dem PC. API-Daten werden nicht durch die PWA offline gecacht. Eine öffentliche Tailscale-Funnel-Freigabe ist nicht vorgesehen.

## Anfänger-Schutz

Unbekannte Lizenzen, ungültige Git-URLs/Refs, Pfade außerhalb des Projekts, unpassende Tokenizer und ungültige Trainingskonfigurationen werden vor dem Start abgewiesen. Große Jobs laufen separat und können gestoppt werden.

## Import-Optionen für große Mengen (v0.10.2)

Unter **Wissensaufbau → Eigene Godot-Projekte · privat** gibt es jetzt drei
Schalter, die sonst nur als Umgebungsvariablen erreichbar waren:

- **Godot-Projektimport überspringen** (`GODOT_CODER_SKIP_PROJECT_IMPORT=1`) –
  prüft jede `.gd`-Datei einzeln statt des vollen `--import`. Deutlich schneller,
  aber ohne Projektkontext; die volle Prüfung läuft später über
  `validate_dataset.py`.
- **Statische AST-Prüfung überspringen** (`GODOT_CODER_FAST_STATIC=1`) –
  überspringt nur die langsame Warnungsanalyse. Der Secret-Scan und die
  Dateigrößenprüfung laufen trotzdem (seit v0.10.2 garantiert).
- **Fehler-Abbruch verschärfen** (`GODOT_CODER_ERROR_ABORT_THRESHOLD=60`) –
  bricht Godot nach 60 statt 500 Fehlerzeilen ab. Nützlich, wenn ein Add-on
  den Import in eine Endlosschleife treibt.

Die Schalter werden als Umgebungsvariablen an den Import-Job durchgereicht
(`extra_env`), also ohne Shell. Die Einstellungen gelten nur für den jeweiligen
Importlauf, nicht dauerhaft.

## Professional Training Core

Die Trainingsansicht enthält eine Preflight-Ampel. Rot blockiert den Nachtlauf, Gelb erlaubt eine bewusste Prüfung und Grün bedeutet, dass Pflichtprüfungen bestanden sind. Erweiterte Optionen bleiben eingeklappt.

## Parser-Fallback

Bei einem festgefahrenen Godot-Mono-Editorimport (Timeout, Idle, Fehlerflut)
wechselt das Studio automatisch auf die dateiweise `--check-only`-Prüfung. Der
aktuelle Pfad, Dateizähler und ausgeschlossene Parserfehler werden im
Live-Fortschritt angezeigt. Seit v0.10.1 beendet der Windows-Job-Object-Runner
den kompletten Prozessbaum, bevor der Fallback startet — keine Waisen mehr.
