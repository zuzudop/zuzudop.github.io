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

- [x] „Ohne Flächenangabe“ verkleinert: Straßenraum bekommt die nächste Fläche im Umkreis von 20 m (`--rand`), Flächen im eigenen Stadtbezirk haben Vorrang.
- [ ] Zuordnung des Straßenraums verfeinern: z. B. Fläche auf beiden Straßenseiten prüfen statt nur die nächste.

## Auswertungen

- [ ] „Wie oft“ (1×/2×/…) je Bezirk – dafür die Wiederholungszählung in `build.py` je Bezirk aufteilen.
- [ ] Zeitreise mit Bezirksauswahl (Bezirk „füllt sich“ über die Zeit).
- [x] Gehzeit je Bezirk – vorerst anteilig nach Strecke geschätzt.
- [ ] Exakte Gehzeit je Bezirk: `build.py` müsste Zeitstempel je Abschnitt in `data.json` mitliefern.
- [ ] Abweichung zu OSM: Liste der größten zusammenhängenden Lücken (z. B. „längste nicht gegangene Wege“) als Anregung für nächste Spaziergänge.

## Seite / Bedienung

- [x] Anordnung der Unterpunkte unter „Abdeckung“ verbessert: Hauptleiste Gebiet, Wegart, Belag, Besonderheit; darunter klein „Weitere: Wie oft, Heatmap, Jahr“. „Nicht gegangene Wege“ (früher „Abweichung zu OSM“) ist ein Schalter innerhalb der vier Merkmale. Statistik in derselben Reihenfolge.

## Technik

- [ ] Laufzeit von `osm_analyse.py` (derzeit gut 4 Minuten) verkürzen, z. B. Zwischenergebnisse aus der `.pbf` zwischenspeichern, wenn sich nur `data.json` ändert.
- [ ] Bezirksteilnamen: Quelle in `bezirksteile.json` gelegentlich gegen die Stadt München abgleichen.
