#!/usr/bin/env python3
"""
Namen der Bezirksteile einmalig von Wikidata holen.

In OpenStreetMap haben die Münchner Bezirksteile (admin_level 10) meist nur
eine Nummer (ref, z. B. "1.1"), aber einen Verweis auf Wikidata. Dieses Skript
liest die Wikidata-IDs aus osm.json, fragt die deutschen Bezeichnungen ab und
schreibt sie nach bezirksteile.json ({"1.1": "Graggenau", …}). Nur 34 der 108
Bezirksteile haben eine Wikidata-ID; die übrigen Namen sind von Hand aus
https://stadtgeschichte-muenchen.de/stadtteil/liste_stadtteile.php ergänzt
und bleiben beim erneuten Aufruf erhalten.
osm_analyse.py übernimmt die Namen von dort – ohne Internetzugriff.

Nur Python-Standardbibliothek. Aufruf (nach osm_analyse.py):
    python bezirksteil_namen.py
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

here = Path(__file__).resolve().parent
src = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "osm.json"
out = here / "bezirksteile.json"

rows = json.loads(src.read_text(encoding="utf-8"))["bezirke"]["10"]
ids = {r["wikidata"]: r["ref"] for r in rows if r.get("wikidata")}
print(f"{len(rows)} Bezirksteile, {len(ids)} mit Wikidata-ID", file=sys.stderr)

names = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
q = list(ids)
for i in range(0, len(q), 50):  # max. 50 IDs pro Anfrage
    url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
        "action": "wbgetentities", "ids": "|".join(q[i:i + 50]), "props": "labels",
        "languages": "de", "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": "walking-muenchen-build/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    for qid, ent in data.get("entities", {}).items():
        lab = ent.get("labels", {}).get("de", {}).get("value")
        if lab:
            names.setdefault(ids[qid], lab)  # vorhandene (von Hand ergänzte) Namen bleiben

names = dict(sorted(names.items(), key=lambda kv: [int(x) if x.isdigit() else -1 for x in kv[0].split(".")]))
out.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
missing = [r["ref"] for r in rows if r["ref"] not in names]
print(f"{len(names)} Namen → {out.name}" + (f"; ohne Namen: {', '.join(missing)}" if missing else ""), file=sys.stderr)
