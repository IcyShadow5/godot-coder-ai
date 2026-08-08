# Installation und Upgrade auf v0.10.2

Aktuelle Anleitung (ersetzt `docs/archive/INSTALL_v0.7.9.md`).

## Neuinstallation

Das Clean ZIP (bzw. der Repo-Checkout) enthält nur Projektcode, Konfigurationen,
Tests, Dokumentation und Startskripte — keine Modelle, Checkpoints, privaten
Quellen, Reports, Datenordner oder virtuelle Umgebung.

Für eine neue Installation die reguläre Python-/CUDA-Einrichtung befolgen und
anschließend im Projektordner ausführen:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m godot_coder.doctor
.\.venv\Scripts\python.exe -m pytest -q
```

## Bestehende Installation upgraden (v0.10.1 → v0.10.2)

1. Laufende Trainings-, Download-, Audit- oder Corpusjobs beenden und das Studio schließen.
2. Das fertige Upgrade-Paket (v0.10.2) verwenden — es wird separat zum Release mitgeliefert und enthält `APPLY_V0102_UPGRADE.ps1` (bzw. `.bat`) plus `payload/`. Das Repo selbst hält in `upgrade/` nur **Templates** (siehe `upgrade/README.md`), um für künftige Releases ein eigenes Paket zu bauen.
3. Falls das Paket noch keine `payload/` enthält: den mitgelieferten `build_v0102_payload.ps1` ausführen (baut die Payload aus dem aktuellen Repo-Stand).
4. `APPLY_V0102_UPGRADE.bat` starten und den Pfad zum bestehenden Godot-Coder-AI-Ordner angeben
   (oder direkt: `APPLY_V0102_UPGRADE.ps1 -ExistingProject "C:\...\CodingAi"`).
5. Nach erfolgreichem Testlauf (doctor + pytest laufen automatisch) das Studio wieder starten.

Der Upgrader überschreibt nur die Dateien aus dem Paket. Er ersetzt oder löscht
niemals `.venv`, `data`, `checkpoints`, `artifacts`, `reports`, `.studio_backups`,
`.upgrade_backups` oder `configs/autotuned_*.yaml`.

Vor jedem Überschreiben wird eine Kopie der tatsächlich ersetzten Dateien unter
`<Projekt>\.upgrade_backups\v0.10.2-<Zeitstempel>` angelegt. Bei einem
fehlgeschlagenen Testlauf bleibt dieses Backup erhalten; es findet kein
automatischer Rollback statt — manuell: Backupdateien zurückkopieren.

## Nach dem Upgrade

```powershell
.\.venv\Scripts\python.exe -m godot_coder.doctor
.\.venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe -m godot_coder.studio
```

`doctor` prüft zusätzlich Godot und die CUDA-Laufzeit. Auf einem Rechner ohne
korrekt installierten Godot- oder CUDA-Pfad kann dieser Check scheitern, obwohl
die reinen Python-Tests bestanden haben.

## Was sich seit v0.10.1 geändert hat

Siehe `docs/CHANGELOG_v0.10.2.md`. Kurzfassung: Secret-Scan läuft jetzt in jedem
Modus (Sicherheitsfix), `SKIP_PROJECT_IMPORT` gewinnt über `FAST_STATIC`,
`validate_dataset.py` nutzt Managed-Process-Runner + env-Timeout, Studio-Toggles
für den schnellen Import; das Upgrade-Tooling liegt als Template im Repo (`upgrade/`).
