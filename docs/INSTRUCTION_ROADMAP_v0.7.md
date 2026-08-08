# Instruction- und Agenten-Roadmap

> Dieser Entwurf stammt aus der v0.7-Phase und ist bis heute die Grundlage für
> die geplanten Stufen. Instruction-Tuning und Agenten sind bewusst noch keine
> Meilensteine von v0.10.x — erst wenn die Domainbasis und die Verifier-Schleife
> stehen. Der aktuelle Stand dazu steht in `ROADMAP.md`.

## Stufe 1 — Domain-Pretraining

Das Basismodell lernt GDScript, Godot-APIs, Projektstrukturen und häufige
Implementierungsmuster aus sauberem Quellcode. Mehr Rohcode verbessert die
Sprach- und Domainbasis, erzeugt aber allein noch keinen zuverlässigen
Aufgabenassistenten.

## Stufe 2 — Supervised Instruction Tuning

Benötigt geprüfte Paare aus:

- Anweisung und gewünschtem Code
- fehlerhaftem Code und Reparatur
- Projektkontext, Plan und Patch
- Aufgabe, Tests und verifiziertem Ergebnis

Der Loss soll später nur auf der Assistentenantwort liegen. Die v0.7-Seedarbeiten
erzeugen dafür deterministische Seed-Aufgaben, markieren sie aber noch nicht als
trainingsfertigen Ersatz für kuratierte Aufgaben.

## Stufe 3 — Verifier und Reparaturschleife

Generierte Lösungen werden mit Godot, Tests und statischen Prüfungen bewertet.
Erfolgreiche Ergebnisse können als hochwertige Nachschulungsdaten übernommen
werden; fehlgeschlagene Ausgaben bleiben getrennt und werden nicht blind
trainiert. Die Bausteine dafür wachsen schon heute: der Managed-Process-Runner,
die Godot-Parserprüfung mit Fallback und der Error-Rate-Abort sind im Prinzip
dieselbe Infrastruktur, die ein Verifier später braucht.

## Stufe 4 — Agentenlaufzeit

Ein Agent ist mehr als das Sprachmodell. Er braucht begrenzte Werkzeuge und
einen kontrollierten Ablauf:

1. Dateien und Projektstatus lesen
2. Aufgabe in überprüfbare Schritte zerlegen
3. Patch erstellen
4. Godot oder Tests ausführen
5. Fehler auswerten und begrenzt reparieren
6. Änderungen und Nachweise berichten

Diese Stufe darf erst auf einer stabilen Instruction- und Verifier-Basis folgen.
