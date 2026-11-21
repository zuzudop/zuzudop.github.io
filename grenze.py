#!/usr/bin/env python3
"""
Stadtgrenze aus einem OSM-Auszug holen und als grenze.json speichern.

Sucht die Verwaltungsgrenze (boundary=administrative) mit dem angegebenen
Namen, setzt die äußeren Wege zu Ringen zusammen und schreibt sie als
Encoded Polylines. index.html zeichnet sie, wenn die Datei vorhanden ist.

Benötigt: pip install numpy protobuf

Aufruf:
    python grenze.py Muenchen.osm.pbf                 # München, admin_level 6
    python grenze.py oberbayern.osm.pbf --name München
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

import osmpbf
from build import encode, simplify


def assemble_rings(ways):
    """Fügt Linien (Listen von Knoten-IDs) zu geschlossenen Ringen zusammen."""
    rings, open_ = [], []
    for w in ways:
        if len(w) < 2:
            continue
        (rings if w[0] == w[-1] else open_).append(list(w))
    while open_:
        cur = open_.pop()
        changed = True
        while cur[0] != cur[-1] and changed:
            changed = False
            for i, w in enumerate(open_):
                if w[0] == cur[-1]:
                    cur += w[1:]
                elif w[-1] == cur[-1]:
                    cur += w[-2::-1]
                elif w[-1] == cur[0]:
                    cur = w[:-1] + cur
                elif w[0] == cur[0]:
                    cur = w[:0:-1] + cur
                else:
                    continue
                open_.pop(i)
                changed = True
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            rings.append(cur)
        else:
            print(f"Warnung: Ring nicht geschlossen ({len(cur)} Knoten), wird als Linie übernommen", file=sys.stderr)
            rings.append(cur)
    return rings


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pbf")
    ap.add_argument("--name", default="München")
    ap.add_argument("--level", default="6", help="admin_level (Standard 6 = kreisfreie Stadt)")
    ap.add_argument("--tol", type=float, default=5.0, help="Vereinfachung in Metern (Standard 5)")
    ap.add_argument("-o", "--out", default=str(here / "grenze.json"))
    a = ap.parse_args()

    # 1) Relation finden
    outer = None
    for pb in osmpbf.blocks(a.pbf):
        st = pb.stringtable.s
        for g in pb.primitivegroup:
            for r in g.relations:
                t = osmpbf.tags_of(st, r.keys, r.vals)
                if (t.get(b"boundary") == b"administrative" and t.get(b"admin_level") == a.level.encode()
                        and t.get(b"name") == a.name.encode()):
                    ids = np.cumsum(np.fromiter(r.memids, np.int64, len(r.memids)))
                    outer = [int(i) for i, rs, ty in zip(ids, r.roles_sid, r.types)
                             if ty == 1 and st[rs] in (b"outer", b"")]
                    print(f"Relation {r.id}: {len(outer)} äußere Wege", file=sys.stderr)
                    break
            if outer is not None:
                break
        if outer is not None:
            break
    if not outer:
        sys.exit(f"Grenze '{a.name}' (admin_level {a.level}) nicht im Auszug gefunden.")

    # 2) Wege
    need = set(outer)
    way_refs = {}
    for pb in osmpbf.blocks(a.pbf):
        for g in pb.primitivegroup:
            for wy in g.ways:
                if wy.id in need:
                    way_refs[wy.id] = np.cumsum(np.fromiter(wy.refs, np.int64, len(wy.refs))).tolist()
    missing = need - way_refs.keys()
    if missing:
        sys.exit(f"{len(missing)} Grenz-Wege fehlen im Auszug – größeren Auszug verwenden (z. B. oberbayern).")

    # 3) Knoten
    node_ids = np.unique(np.concatenate([np.array(v, np.int64) for v in way_refs.values()]))
    coord = {}
    for pb in osmpbf.blocks(a.pbf):
        for g in pb.primitivegroup:
            if g.HasField("dense") and len(g.dense.id):
                ids, la, lo = osmpbf.dense_nodes(pb, g)
                m = np.isin(ids, node_ids)
                for i, y, x in zip(ids[m].tolist(), la[m].tolist(), lo[m].tolist()):
                    coord[i] = (y, x)
    if len(coord) < len(node_ids):
        sys.exit(f"{len(node_ids) - len(coord)} Grenz-Knoten fehlen im Auszug – größeren Auszug verwenden.")

    rings = assemble_rings([way_refs[i] for i in outer])
    lines = [simplify([coord[n] for n in ring], a.tol) for ring in rings]
    out = {"name": a.name, "rings": [encode(l) for l in lines]}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(rings)} Ring(e), {sum(map(len, lines)):,} Punkte → {a.out} "
          f"({Path(a.out).stat().st_size / 1024:,.0f} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
