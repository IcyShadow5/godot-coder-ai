# Instruction- und Agenten-Roadmap

## Stufe 1 — Domain-Pretraining

Das Basismodell lernt GDScript, Godot-APIs, Projektstrukturen und häufige Implementierungsmuster aus sauberem Quellcode. Mehr Rohcode verbessert die Sprach- und Domainbasis, erzeugt aber allein noch keinen zuverlässigen Aufgabenassistenten.

## Stufe 2 — Supervised Instruction Tuning

Benötigt geprüfte Paare aus:

- Anweisung und gewünschtem Code
- fehlerhaftem Code und Reparatur
- Projektkontext, Plan und Patch
- Aufgabe, Tests und verifiziertem Ergebnis

Der Loss soll später nur auf der Assistentenantwort liegen. v0.7 erzeugt dafür deterministische Seed-Aufgaben, markiert sie aber noch nicht als trainingsfertigen Ersatz für kuratierte Aufgaben.

## Stufe 3 — Verifier und Reparaturschleife

Generierte Lösungen werden mit Godot, Tests und statischen Prüfungen bewertet. Erfolgreiche Ergebnisse können als hochwertige Nachschulungsdaten übernommen werden; fehlgeschlagene Ausgaben bleiben getrennt und werden nicht blind trainiert.

## Stufe 4 — Agentenlaufzeit

Ein Agent ist mehr als das Sprachmodell. Er braucht begrenzte Werkzeuge und einen kontrollierten Ablauf:

1. Dateien und Projektstatus lesen
2. Aufgabe in überprüfbare Schritte zerlegen
3. Patch erstellen
4. Godot oder Tests ausführen
5. Fehler auswerten und begrenzt reparieren
6. Änderungen und Nachweise berichten

Diese Stufe darf erst auf einer stabilen Instruction- und Verifier-Basis folgen.
