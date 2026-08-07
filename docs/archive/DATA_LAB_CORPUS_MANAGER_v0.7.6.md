# Data Lab & Corpus Manager v0.7.6

## Welche Daten Data Lab zeigt

Data Lab unterscheidet vier Zustände:

- **Aktiver Tokenstream:** Dokumente, die im letzten vorbereiteten Train-/Validation-/Test-Manifest enthalten sind. Für jedes Dokument werden die tatsächlich verwendeten Tokens angezeigt.
- **Neu/pending:** neue Roh- oder auditierte Dateien, die noch nicht erneut tokenisiert wurden.
- **Rohdaten:** eigene Dateien unter `data/raw`; diese sind bearbeitbar und löschbar.
- **Aufgabendaten:** erzeugte Instruction-Beispiele. Sie werden als Aufgaben gezählt und nicht als neue unabhängige Corpusvielfalt ausgegeben.

Die frühere Curriculum-Anzeige kann den Corpus-Tokenwert nicht mehr überschreiben.

## Löschen

Nur eigene Dateien unter `data/raw` können direkt gelöscht werden. Das verhindert, dass generierte Shards, auditierte Quellen oder abgeleitete Aufgaben inkonsistent einzeln verändert werden.

Vor der Löschung entsteht ein Backup unter:

```text
.studio_backups/data_lab/deleted/
```

Nach einer Löschung oder Änderung gilt der bisher vorbereitete Tokenstream als veraltet. Die Datenpipeline muss ab dem betroffenen Schritt erneut ausgeführt werden.

## Hot Reload

Solange Data Lab geöffnet ist, fragt die Oberfläche alle 2,5 Sekunden eine kompakte Revision ab. Neue, geänderte oder gelöschte Dateien werden ohne kompletten Seitenneustart übernommen. Ungespeicherte Editoränderungen werden dabei nicht überschrieben.

## Große Corpus-Erweiterung

Der neue Katalog enthält 30 Quellen. 23 davon bilden die neue Erweiterung und starten bei bestehenden Installationen deaktiviert.

- **5M-Kandidaten:** acht größere Quellen, Katalogschätzung 6,0 Mio. Tokens.
- **Richtung 20M:** zusätzlich 15 weitere Quellen, Gesamtschätzung 12,44 Mio. Tokens.

Die Schätzung basiert auf erwarteter GDScript-Menge und ist kein gemessener Endwert. Erst der lokale Ablauf liefert den belastbaren Wert:

1. Lizenz lokal verifizieren
2. Quellcode selektiv laden
3. Godot-4-Dateien erkennen
4. Parserprüfung
5. Duplikate und Leaks entfernen
6. Split bilden
7. mit dem aktuellen BPE-Tokenizer zählen

Eine Quelle mit fehlender oder nicht passender Lizenzdatei wird nicht in den Corpus aufgenommen.

## Speicher- und Downloadverhalten

Große Repositories werden mit partiellem Git-Clone und Sparse Checkout geladen. Standardmäßig werden nur Godot-Projektdateien, GDScript und wenige textbasierte Ressourcen angefordert. Bilder, Audio, Videos, Buildordner und Caches sind nicht Teil des Trainingscorpus.
