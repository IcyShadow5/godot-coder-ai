# Audit v0.5.0

> **Veraltet / Archiv.** Historischer Audit der v0.5-Phase. Der Nachfolger ist
> `docs/AUDIT_v0.6.md`; alles seit v0.6 steht im `docs/CHANGELOG_v0.10.2.md`.

## Behobene Fehler und Risiken

- Git-Quellen verwenden reproduzierbare Branches/Tags/Commits; unvollständige Downloads gelten nicht mehr als fertig.
- Änderungen am Source-Ref werden erkannt und als erneuter Download markiert.
- Korpus-Staging und vorbereitete Splits werden atomar aus einem Build-Ordner ersetzt.
- Datensatzdateien werden atomar geschrieben, gehasht und gegen Manifestlänge geprüft.
- Token-Streams wechseln bei großen Vokabularen automatisch von `uint16` auf `uint32`.
- Sampling kann nun auch den letzten gültigen Kontextstart verwenden.
- Training löst relative Pfade gegen die tatsächlich übergebene Konfiguration auf, nicht gegen ein zufällig installiertes Checkout.
- Trainingsdurchsatz misst nur Trainingszeit; Validation und Checkpoint-I/O werden separat ausgewiesen.
- Resume- und Teilintervalle erzeugen korrekte Token- und Schrittzahlen.
- `latest.pt` und `best.pt` verwenden nach Möglichkeit Hardlinks; alte Step-Checkpoints werden begrenzt aufbewahrt.
- VRAM-Probeläufe behandeln frühe OOMs ohne Folgefehler und laufen pro Profil isoliert.
- Das im Chat gecachte Modell wird vor Training, Benchmark und VRAM-Probe aus GPU/RAM entladen.
- Stop beendet unter Windows den vollständigen Prozessbaum, einschließlich Git- und Probe-Kindprozessen.
- Generierung und Evaluation übernehmen Tokenizer und Rechenpräzision aus dem Checkpoint.
- Temporäre Godot-Prüfdateien und Konfigurationsbackups sind kollisionssicher.
- Das Studio priorisiert den echten Corpus-Datensatz vor alten Demo-Manifests.
- Eigene Git-Quellen besitzen ein sichtbares Ref-Feld statt eines stillen `master`-Defaults.
- Windows-Konsolenausgaben sind ASCII-sicher; JSON-Dateien bleiben UTF-8.
- FastAPI-Shutdown nutzt einen Lifespan-Handler statt der veralteten Event-API.

## Bewusste Grenzen

- v0.5 baut noch kein Agentensystem und kein autonomes Projekt-Editing.
- Öffentliche Repositories werden nur bei erlaubter, deklarierter Lizenz aufgenommen.
- Der Godot-Validator prüft Syntax/API-Auflösung, aber nicht automatisch vollständige Gameplay-Semantik.
- Ein größerer Datensatz verbessert das Modell nur zusammen mit sinnvoller Diversität, Splits und Trainingsformaten.
