# 🍀 Glücksgrube

Multiplayer-Slot-Roguelike für dich und deine Freunde (Flask).
Dreh den Automaten, zahl den Eintreiber, kauf Glücksbringer – und sabotiere deine Mitspieler.

## Starten
    pip install -r requirements.txt
    python Glucksgrube.py
Dann `http://localhost:5000` öffnen. Freunde im selben WLAN: die Adresse, die beim Start in der Konsole steht
(`http://<deine-IP>:5000`). Über das Internet geht es mit einer Port-Freigabe oder einem Tunnel (ngrok, Tailscale).

## Spielidee
- Jede Runde hast du 5 Dreh am Automaten (5 Walzen × 3 Reihen, 5 Gewinnlinien). Gewinne = Münzen 🪙.
- Am Rundenende kommt der **Eintreiber 💀** und will Schulden (18, 26, 36, 50, … – sie wachsen jede Runde).
  Wer nicht zahlen kann, verliert ein ❤️. Zwei Leben, dann bist du raus.
- Dazwischen: **Schwarzmarkt** mit 18 Glücksbringern (Symbole aufwerten, Extra-Dreh, Zinsen, Joker, Schuldenstreicher, …).
- **Halten 🔒** (Walze behalten), **Risiko 🎲** (Gewinn verdoppeln, ~45 %), **gemeinsamer Jackpot 🎰** (3× 7️⃣ irgendwo).
- **Sabotage:** Taschendieb 🧲, Frostbeule 🧊, Pechfluch 🕷️ – und der Zauberspiegel 🪞 wirft sie zurück.
- Letzter Überlebender gewinnt. Allein spielen geht auch (Solo-Modus).
- Konten (Name + PIN), Level, Ruhm und Bestenliste werden in `spielstand.json` gespeichert.

## Dateien
| Datei | Inhalt |
|---|---|
| `Glucksgrube.py` | Server (Flask, `os`, `threading`), Spiellogik, Speicherung |
| `index.html` | Oberfläche |
| `static/style.css` | Neon-Design |
| `static/app.js` | Client: Animationen, Sound (WebAudio), Partikel |
| `test_glucksgrube.py` | Tests (`pip install pytest && pytest -q`) |
| `spielstand.json` | wird automatisch angelegt, nicht ins Repo (`.gitignore`) |

Einstellungen per Umgebungsvariable: `HOST`, `PORT`, `GRUBE_DATA` (Pfad der JSON-Datei).
Die Zahlen zum Balancing stehen oben in `Glucksgrube.py` (Schulden, Preise, Gewichte, Auszahlungen).
