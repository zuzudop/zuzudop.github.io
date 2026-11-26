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
- `--rep-tol` = Vereinfachung der „Wie oft“-Linien in Metern (Standard 0,5). Bewusst kleiner als `--tol`: Bei 3 m schneiden die Linien kurze Ecken ab und enden neben dem Weg.

## Wie die Daten gruppiert werden

- Ein Spaziergang = alle Segmente desselben Kalendertags (Europe/Berlin).
- Segmente ohne Zeitstempel landen in „ohne Datum“ (erscheinen in Abdeckung/Heatmap und Liste, nicht in der Zeitreise).
- Punkte ohne Zeit innerhalb einer Aufzeichnung erben den Tag des Punkts davor; ganze Stücke ohne Zeit kommen zu dem Tag, an dessen Weg sie (≤ 60 m) anschließen.
- Wiederholungen: Wege werden chronologisch alle 2 m abgetastet; liegt ein Punkt ≤ 3 m neben einem schon gegangenen Weg, zählt er als Wiederholung – auch am selben Tag (Sackgasse rein und raus, Runde zweimal). Jede Begehung zählt einzeln. Die eigene Spur der letzten Meter zählt nicht: Ein Punkt desselben Tags gilt erst als „schon gegangen“, wenn der Weg seitdem mindestens 6 m länger ist als die Luftlinie (Umkehr). An Wendepunkten bleiben dadurch nur etwa 3 m unerkannt. Daraus: km ohne Wiederholung (jedes Wegstück nur einmal), neue km pro Spaziergang, Karte „Wie oft“.
- Gehzeit = Summe der Zeitabstände unter 15 Minuten; längere Lücken zählen als Pause.

## Lokal ansehen

`fetch()` braucht einen Webserver: `python -m http.server` im Repo-Ordner, dann http://localhost:8000 öffnen.

## Stadtgrenze

`grenze.json` enthält die Stadtgrenze von München (OSM-Relation 62428). Die Karte zeichnet sie gestrichelt unter den Wegen; abschaltbar im Ebenen-Menü oben rechts. Sie wird von `osm_analyse.py` mit erzeugt (siehe unten), Option `--stadt` für eine andere Stadt.

## OSM-Auswertung (Wegart, Gebiet, Belag)

`osm_analyse.py` ordnet jeden gegangenen Meter einer Wegart (Gehweg, Fußweg, Pfad, Trampelpfad, Straße …), einem Gebiet (Friedhof, Spielplatz, Park, Wald …) und einem Belag zu und schreibt `osm.json`. Die Seite lädt diese Datei, wenn sie vorhanden ist (Abdeckung → Wegart/Gebiet/Bezirke, Statistik, Zeitreise).

Stadtbezirke (OSM admin_level 9) und Bezirksteile (admin_level 10) werden im selben Durchlauf ausgewertet – je Gebiet:
- **Fläche** in km²
- **Wegedichte** = eigene Wege (km ohne Wiederholung) / Fläche in km/km² (berechnet auf der Seite)
- gegangene km (mit/ohne Wiederholung), Anzahl Spaziergänge und erster Besuch (ab 100 m im Gebiet)
- Gehzeit, anteilig geschätzt: Minuten je Spaziergang × Anteil seiner Strecke im Gebiet
- km je Wegart, Gebiet und Belag – auf der Seite über die Auswahl „Bezirk“ (Abdeckung und Statistik) einzeln ansehbar

**Besonderheit** (Ansicht unter Abdeckung, Balken unter Statistik): Eigenschaft des nächsten OSM-Wegs, quer zu Wegart und Gebiet – Bahnsteig (`public_transport`/`railway=platform`), Tunnel/unterirdisch (`tunnel`, `layer`/`level` < 0, `location=underground`), Innenraum/Passage (`indoor`, `tunnel=building_passage`, `covered`), Brücke/Steg (`bridge`), Straßenquerung (`footway=crossing`), eingeschränkter Zugang (`access`/`foot=customers|destination|…`). Treppen stehen unter Wegart.

**Abweichung zu OSM** (`osm_luecken.json`, Ansicht unter Abdeckung): begehbare OSM-Wege in der Stadt, an denen kein Spaziergang höchstens 12 m (`--match`) entfernt vorbeiführt, als Linien nach Gebiet, Wegart oder Besonderheit, für die ganze Stadt und je Bezirk. Ein Schalter blendet die eigene Strecke gestrichelt ein. Nicht gezählt werden Autobahnen/Kraftfahrstraßen, private Wege, Parkplatzgassen und Einfahrten; Stücke unter 10 m werden weggelassen. Die Datei wird erst geladen, wenn die Ansicht geöffnet wird.

Die Namen der Bezirksteile stehen in `bezirksteile.json` (OSM hat dort meist nur Nummern), siehe `bezirksteil_namen.py`.

1. OSM-Auszug besorgen, z. B. `Muenchen.osm.pbf` von https://download.bbbike.org/osm/bbbike/Muenchen/
2. Einmalig: `pip install numpy scipy matplotlib protobuf`
3. Nach `python build.py` ausführen: `python osm_analyse.py Muenchen.osm.pbf` (ca. 1 Minute)
4. `osm.json` mit hochladen. **Die .pbf-Dateien nicht ins Repo hochladen** (zu groß, GitHub erlaubt max. 100 MB pro Datei).

Gebiet: OSM-Flächen enden meist am Straßenrand. Liegt ein Weg in keiner Fläche, bekommt er die nächste Fläche im Umkreis von 20 m (`--rand`); kommen mehrere infrage, gewinnt eine im selben Stadtbezirk.

Hinweise: Es werden die heutigen OSM-Daten verwendet. Wegart = nächster OSM-Weg im Umkreis von 12 m; Stücke unter 10 m (z. B. Straßenquerungen) übernehmen die Kategorie davor. Bei überlappenden Flächen zählt die speziellere.
