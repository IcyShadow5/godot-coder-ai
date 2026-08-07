# Installation und Upgrade auf v0.7.9

## Clean ZIP

Das Clean ZIP enthält nur Projektcode, Konfigurationen, Tests, Dokumentation und Startskripte. Es enthält keine Modelle, Checkpoints, privaten Quellen, Reports, Datenordner oder virtuelle Umgebung.

Für eine neue Installation die reguläre Python-/CUDA-Einrichtung befolgen und anschließend im Projektordner ausführen:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

## Bestehende Installation upgraden

1. Laufende Trainings-, Download-, Audit- oder Corpusjobs beenden und das Studio schließen.
2. Das Upgrader-ZIP in einen neuen Ordner entpacken.
3. `APPLY_CUMULATIVE_V079_UPGRADE.bat` starten.
4. Den bestehenden Godot-Coder-AI-Ordner auswählen.
5. Nach erfolgreichem Testlauf das Studio erneut starten.

Der Upgrader überschreibt nur die Dateien aus dem Paket. Er ersetzt oder löscht niemals `.venv`, `data`, `checkpoints`, `artifacts`, `reports`, `.studio_backups` oder frühere `.upgrade_backups`.

Vor jedem Überschreiben wird eine Kopie der tatsächlich ersetzten Dateien unter `<Projekt>\.upgrade_backups\v0.7.9-<Zeitstempel>` angelegt. Bei einem fehlgeschlagenen Testlauf bleibt dieses Backup erhalten; es findet kein automatischer Rollback statt.

## Nach dem Upgrade

```powershell
.\.venv\Scripts\python.exe -m godot_coder.doctor
.\.venv\Scripts\python.exe -m pytest -q
.\start_studio.bat
```

`doctor` prüft zusätzlich Godot und die CUDA-Laufzeit. Auf einem Rechner ohne korrekt installierten Godot- oder CUDA-Pfad kann dieser Check scheitern, obwohl die reinen Python-Tests bestanden haben.
