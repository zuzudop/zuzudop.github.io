# Walking München

Statische Webseite für https://zuzudop.github.io – alle Spaziergänge durch München auf einer Karte.

## Dateien

| Datei        | Zweck |
|--------------|-------|
| `index.html` | Die komplette Seite (Leaflet, Zeitreise, Abdeckung/Heatmap, Statistik, Tour-Liste) |
| `data.json`  | Kompakte Wegedaten, erzeugt von `build.py` – nicht von Hand bearbeiten |
| `build.py`   | Wandelt alle GPX-Dateien aus `../Walking` in `data.json` um (nur Python-Standardbibliothek) |
| `map.html`   | Weiterleitung, damit alte Links auf `map.html` weiter funktionieren |

## Neue Spaziergänge hinzufügen

1. GPX-Datei(en) in den Ordner `Walking` legen (Unterordner sind ok, doppelte Segmente werden erkannt).
2. Im Ordner `site` ausführen:
   ```
   python build.py
   ```
3. Den Inhalt von `site` ins Repo `zuzudop.github.io` kopieren, committen und pushen.

Optionen: `python build.py PFAD -o data.json --tol 3` (`--tol` = Vereinfachung in Metern, 0 = aus).

## Wie die Daten gruppiert werden

- Ein Spaziergang = alle Segmente desselben Kalendertags (Europe/Berlin).
- Segmente ohne Zeitstempel landen in „ohne Datum“ (erscheinen in Abdeckung/Heatmap und Liste, nicht in der Zeitreise).
- Gehzeit = Summe der Zeitabstände unter 15 Minuten; längere Lücken zählen als Pause.

## Lokal ansehen

`fetch()` braucht einen Webserver: `python -m http.server` im Ordner `site`, dann http://localhost:8000 öffnen.
