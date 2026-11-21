# Ideen und Zukunftspläne

Sammlung offener Ideen für Walking München. Erledigtes einfach löschen oder abhaken.

## OSM-Metadaten weiter erforschen

- [ ] **Metainfos wie `level`, `layer`, `tunnel`, `indoor`, `location` genauer untersuchen** – welche Werte kommen im Münchner Wegenetz vor, wie häufig, wie verlässlich sind sie gepflegt, und welche Auswertungen lassen sich daraus ableiten?
  - Erste Bestandsaufnahme: alle vorkommenden Werte und km je Wert auflisten (eigene Wege und OSM-Netz).
  - „Tunnel/unterirdisch“ aufteilen in **Unterführung/Tunnel** (`tunnel=yes`, meist ohne `level`) und **U-Bahnhof/Tiefgeschoss** (`level` < 0, `location=underground`). Unterführungen werden bewusst gegangen, U-Bahnhöfe nicht.
  - Stockwerke über 0 (`level` > 0): Brücken, Stege, Passagen im Obergeschoss?
  - U-Bahn-Bahnsteige sind meist `highway=platform` und fehlen deshalb im Wegenetz – prüfen, ob sie als eigene Besonderheit sinnvoll sind.
- [ ] Weitere Tags prüfen: `crossing=*` (Ampel/Zebrastreifen), `lit` (beleuchtet), `incline` (Steigung), `smoothness`, `wheelchair`, `access`-Varianten.
- [ ] Brücken über Straßen: Wird ein Weg auf der Brücke manchmal der Straße darunter zugeordnet (12-m-Radius)? `layer` könnte helfen.

- [ ] **„Ohne Flächenangabe“ verkleinern:** In OSM enden Flächen wie Wohngebiet meist an der Bordsteinkante, der Straßenraum liegt in keiner Fläche. Straßen könnten der angrenzenden Fläche zugeordnet werden (z. B. nächste Fläche innerhalb von 15–20 m oder die Fläche auf beiden Seiten).

## Auswertungen

- [ ] „Wie oft“ (1×/2×/…) je Bezirk – dafür die Wiederholungszählung in `build.py` je Bezirk aufteilen.
- [ ] Zeitreise mit Bezirksauswahl (Bezirk „füllt sich“ über die Zeit).
- [ ] Gehzeit je Bezirk (braucht Zeitstempel je Abschnitt statt je Spaziergang).
- [ ] Abweichung zu OSM: Liste der größten zusammenhängenden Lücken (z. B. „längste nicht gegangene Wege“) als Anregung für nächste Spaziergänge.

## Seite / Bedienung

- [ ] **Unterpunkte unter „Abdeckung“ besser anordnen** und nach Relevanz sortieren (derzeit Jahr, Wie oft, Wegart, Gebiet, Besonderheit, Heatmap, Abweichung zu OSM nebeneinander).

## Technik

- [ ] Laufzeit von `osm_analyse.py` (derzeit gut 4 Minuten) verkürzen, z. B. Zwischenergebnisse aus der `.pbf` zwischenspeichern, wenn sich nur `data.json` ändert.
- [ ] Bezirksteilnamen: Quelle in `bezirksteile.json` gelegentlich gegen die Stadt München abgleichen.
