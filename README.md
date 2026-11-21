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
2. Im Repo-Ordner (`zuzudop.github.io`, neben `Walking`) ausführen:
   ```
   python build.py
   ```
3. Committen und pushen: `git add -A`, `git commit -m "Neue Spaziergänge"`, `git push`.

Optionen: `python build.py PFAD -o data.json --tol 3 --rep 3`
- `--tol` = Vereinfachung der Linien in Metern (0 = aus)
- `--rep` = Abstand in Metern, bis zu dem ein Weg als erneut gegangen gilt (Standard 3)

## Wie die Daten gruppiert werden

- Ein Spaziergang = alle Segmente desselben Kalendertags (Europe/Berlin).
- Segmente ohne Zeitstempel landen in „ohne Datum“ (erscheinen in Abdeckung/Heatmap und Liste, nicht in der Zeitreise).
- Punkte ohne Zeit innerhalb einer Aufzeichnung erben den Tag des Punkts davor; ganze Stücke ohne Zeit kommen zu dem Tag, an dessen Weg sie (≤ 60 m) anschließen.
- Wiederholungen: Wege werden chronologisch alle 2 m abgetastet; liegt ein Punkt ≤ 3 m neben einem schon gegangenen Weg, zählt er als Wiederholung (auch hin und zurück am selben Tag). Daraus: km ohne Wiederholung, neue km pro Spaziergang, Karte „Wie oft“.
- Gehzeit = Summe der Zeitabstände unter 15 Minuten; längere Lücken zählen als Pause.

## Lokal ansehen

`fetch()` braucht einen Webserver: `python -m http.server` im Repo-Ordner, dann http://localhost:8000 öffnen.

## OSM-Auswertung (Wegart, Gebiet, Belag)

`osm_analyse.py` ordnet jeden gegangenen Meter einer Wegart (Gehweg, Fußweg, Pfad, Trampelpfad, Straße …), einem Gebiet (Friedhof, Spielplatz, Park, Wald …) und einem Belag zu und schreibt `osm.json`. Die Seite lädt diese Datei, wenn sie vorhanden ist (Abdeckung → Wegart/Gebiet, Statistik, Zeitreise).

1. OSM-Auszug besorgen, z. B. `Muenchen.osm.pbf` von https://download.bbbike.org/osm/bbbike/Muenchen/
2. Einmalig: `pip install numpy scipy matplotlib protobuf`
3. Nach `python build.py` ausführen: `python osm_analyse.py Muenchen.osm.pbf` (ca. 1 Minute)
4. `osm.json` mit hochladen. **Die .pbf-Dateien nicht ins Repo hochladen** (zu groß, GitHub erlaubt max. 100 MB pro Datei).

Hinweise: Es werden die heutigen OSM-Daten verwendet. Wegart = nächster OSM-Weg im Umkreis von 12 m; Stücke unter 10 m (z. B. Straßenquerungen) übernehmen die Kategorie davor. Bei überlappenden Flächen zählt die speziellere.
