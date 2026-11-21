#!/usr/bin/env python3
"""
Walking München – OSM-Auswertung

Ordnet jeden gegangenen Meter einer Wegart (Gehweg, Pfad, Straße …), einem
Belag und einem Gebiet (Friedhof, Spielplatz, Park …) aus OpenStreetMap zu.

Eingaben:  data.json (von build.py) und ein OSM-Auszug als .osm.pbf
Ausgabe:   osm.json (wird von index.html geladen, falls vorhanden)

Benötigt:  pip install numpy scipy matplotlib protobuf
Aufruf:    python osm_analyse.py Muenchen.osm.pbf
"""
import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MPath
from scipy.spatial import cKDTree

import osmpbf
from build import simplify

LAT0 = 48.14
KX = math.cos(math.radians(LAT0)) * 111320.0
KY = 110574.0

# ---------------------------------------------------------------- Kategorien
WEGART = [  # (Schlüssel, Bezeichnung)
    ("gehweg", "Gehweg an Straße"),
    ("fussweg", "Fußweg"),
    ("fussgaengerzone", "Fußgängerzone/Platz"),
    ("pfad", "Pfad"),
    ("trampelpfad", "Trampelpfad"),
    ("treppe", "Treppe"),
    ("radweg", "Rad-/Fußweg"),
    ("feldweg", "Feld-/Waldweg"),
    ("wohnstrasse", "Wohn-/Nebenstraße"),
    ("hauptstrasse", "Hauptstraße"),
    ("keiner", "kein Weg in OSM"),
]
GEBIET = [  # Reihenfolge = Vorrang bei Überlappung
    ("spielplatz", "Spielplatz"),
    ("friedhof", "Friedhof"),
    ("kleingarten", "Kleingärten"),
    ("sport", "Sportanlage"),
    ("park", "Park/Grünanlage"),
    ("wald", "Wald"),
    ("wiese", "Wiese/Feld"),
    ("wasser", "Gewässer/Ufer"),
    ("bildung", "Schule/Uni/Klinik"),
    ("gewerbe", "Gewerbe/Industrie"),
    ("bahn", "Bahngelände"),
    ("wohngebiet", "Wohngebiet"),
    ("sonstiges", "ohne Flächenangabe"),
]
BELAG = [("fest", "befestigt"), ("lose", "unbefestigt"), ("unbekannt", "unbekannt")]
G_PRIO = {k: i for i, (k, _) in enumerate(GEBIET)}

PAVED = {"asphalt", "paved", "paving_stones", "concrete", "concrete:plates", "concrete:lanes", "sett",
         "cobblestone", "unhewn_cobblestone", "metal", "wood", "rubber", "tartan", "bricks", "brick",
         "stone", "artificial_turf", "acrylic"}
UNPAVED = {"unpaved", "gravel", "fine_gravel", "compacted", "dirt", "earth", "ground", "grass", "sand",
           "mud", "pebblestone", "woodchips", "grass_paver", "rock", "clay"}
ROADS_MAIN = {"motorway", "trunk", "primary", "secondary", "tertiary", "motorway_link", "trunk_link",
              "primary_link", "secondary_link", "tertiary_link", "unclassified", "road", "busway"}
ROADS_MINOR = {"residential", "living_street", "service"}
SKIP_HW = {"proposed", "construction", "abandoned", "platform", "bus_stop", "elevator", "corridor",
           "razed", "disused", "raceway", "emergency_bay", "rest_area", "services"}


def wegart(t):
    hw = t.get("highway")
    fw = t.get("footway", "")
    if hw in ("footway", "path", "cycleway") and fw in ("sidewalk",):
        return "gehweg"
    if hw == "footway":
        return "trampelpfad" if t.get("informal") == "yes" else "fussweg"
    if hw == "path":
        if t.get("informal") == "yes":
            return "trampelpfad"
        if t.get("bicycle") in ("designated",) or t.get("segregated"):
            return "radweg"
        return "pfad"
    if hw == "steps":
        return "treppe"
    if hw == "pedestrian":
        return "fussgaengerzone"
    if hw in ("cycleway", "bridleway"):
        return "radweg"
    if hw == "track":
        return "feldweg"
    if hw in ROADS_MINOR:
        return "wohnstrasse"
    if hw in ROADS_MAIN:
        return "hauptstrasse"
    return "fussweg"


def belag(t, art):
    s = t.get("surface", "").split(";")[0]
    if s in PAVED:
        return "fest"
    if s in UNPAVED:
        return "lose"
    tt = t.get("tracktype")
    if tt == "grade1":
        return "fest"
    if tt in ("grade2", "grade3", "grade4", "grade5"):
        return "lose"
    if art in ("hauptstrasse", "wohnstrasse", "gehweg", "fussgaengerzone", "treppe"):
        return "fest"  # in München praktisch immer befestigt
    return "unbekannt"


def gebiet(t):
    lu, le, am, na = t.get("landuse"), t.get("leisure"), t.get("amenity"), t.get("natural")
    if le == "playground":
        return "spielplatz"
    if lu == "cemetery" or am == "grave_yard":
        return "friedhof"
    if lu == "allotments":
        return "kleingarten"
    if le in ("pitch", "sports_centre", "stadium", "track", "golf_course", "ice_rink", "swimming_area"):
        return "sport"
    if le in ("park", "garden", "dog_park") or lu in ("recreation_ground", "village_green"):
        return "park"
    if lu == "forest" or na == "wood":
        return "wald"
    if lu in ("meadow", "farmland", "grass", "orchard", "vineyard", "greenfield") or \
            na in ("grassland", "scrub", "heath", "wetland"):
        return "wiese"
    if na == "water" or lu in ("basin", "reservoir") or t.get("waterway") == "riverbank":
        return "wasser"
    if am in ("school", "university", "college", "kindergarten", "hospital") or lu == "education":
        return "bildung"
    if lu in ("commercial", "retail", "industrial"):
        return "gewerbe"
    if lu == "railway":
        return "bahn"
    if lu == "residential":
        return "wohngebiet"
    return None


AREA_KEYS = ("landuse", "leisure", "amenity", "natural", "waterway")


# ---------------------------------------------------------------- Hilfen
def decode(s):
    pts, i, lat, lon = [], 0, 0, 0
    while i < len(s):
        vals = []
        for _ in (0, 1):
            r = sh = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                r |= (b & 31) << sh
                sh += 5
                if b < 32:
                    break
            vals.append(~(r >> 1) if r & 1 else r >> 1)
        lat += vals[0]
        lon += vals[1]
        pts.append((lat / 1e5, lon / 1e5))
    return pts


def encode(pts, prec=5):
    f = 10 ** prec
    out, plat, plon = [], 0, 0
    for lat, lon in pts:
        ilat, ilon = round(lat * f), round(lon * f)
        for v in (ilat - plat, ilon - plon):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def densify(xy, step):
    """xy: (n,2) Meter. Liefert Punkte alle `step` m und Länge je Punkt."""
    d = np.diff(xy, axis=0)
    L = np.hypot(d[:, 0], d[:, 1])
    n = np.maximum(1, (L / step).astype(int))
    rep = np.repeat(np.arange(len(L)), n)
    k = np.concatenate([np.arange(m) for m in n]) if len(n) else np.array([], int)
    t = (k / n[rep])[:, None]
    pts = xy[rep] + t * d[rep]
    return pts, (L / n)[rep]


def assemble_rings(ways):
    """Fügt Linien (Listen von Knoten-IDs) zu geschlossenen Ringen zusammen."""
    rings, open_ = [], []
    for w in ways:
        if len(w) < 2:
            continue
        if w[0] == w[-1]:
            rings.append(list(w))
        else:
            open_.append(list(w))
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
    return rings


# ---------------------------------------------------------------- Hauptteil
def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pbf", help="OSM-Auszug (.osm.pbf), z. B. Muenchen.osm.pbf von BBBike")
    ap.add_argument("--data", default=str(here / "data.json"))
    ap.add_argument("-o", "--out", default=str(here / "osm.json"))
    ap.add_argument("--match", type=float, default=12.0, help="max. Abstand zu einem OSM-Weg in m (Standard 12)")
    a = ap.parse_args()
    T = time.time()
    log = lambda *x: print(f"[{time.time() - T:5.1f}s]", *x, file=sys.stderr)

    data = json.loads(Path(a.data).read_text(encoding="utf-8"))
    walks = data["walks"]
    lines = [(wi, np.array(decode(s))) for wi, w in enumerate(walks) for s in w["p"]]
    allpts = np.concatenate([l for _, l in lines])
    s, n = allpts[:, 0].min() - .01, allpts[:, 0].max() + .01
    w_, e = allpts[:, 1].min() - .015, allpts[:, 1].max() + .015
    log(f"{len(walks)} Spaziergänge, Bereich {s:.3f}–{n:.3f} N, {w_:.3f}–{e:.3f} O")

    # --- Durchlauf 1: Relationen (Multipolygone)
    rels = []
    need_ways = set()
    for pb in osmpbf.blocks(a.pbf):
        st = pb.stringtable.s
        for g in pb.primitivegroup:
            for r in g.relations:
                t = {st[k].decode(): st[v].decode() for k, v in zip(r.keys, r.vals)}
                if t.get("type") != "multipolygon":
                    continue
                cat = gebiet(t)
                if not cat:
                    continue
                ids = np.cumsum(np.fromiter(r.memids, np.int64, len(r.memids)))
                mem = [(int(i), st[rs].decode()) for i, rs, ty in zip(ids, r.roles_sid, r.types) if ty == 1]
                rels.append((cat, mem))
                need_ways.update(i for i, _ in mem)
    log(f"{len(rels)} Gebiets-Relationen")

    # --- Durchlauf 2: Knoten im Bereich, Wege, Flächen
    nid, nlat, nlon = [], [], []
    hw_ways, area_ways, member_refs = [], [], {}
    for pb in osmpbf.blocks(a.pbf):
        st = pb.stringtable.s
        for g in pb.primitivegroup:
            if g.HasField("dense") and len(g.dense.id):
                ids, la, lo = osmpbf.dense_nodes(pb, g)
                m = (la >= s) & (la <= n) & (lo >= w_) & (lo <= e)
                if m.any():
                    nid.append(ids[m]); nlat.append(la[m]); nlon.append(lo[m])
            for wy in g.ways:
                refs = None
                t = {st[k].decode(): st[v].decode() for k, v in zip(wy.keys, wy.vals)}
                if wy.id in need_ways:
                    refs = np.cumsum(np.fromiter(wy.refs, np.int64, len(wy.refs)))
                    member_refs[wy.id] = refs
                hw = t.get("highway")
                if hw and hw not in SKIP_HW and (t.get("area") != "yes" or hw == "pedestrian"):
                    if refs is None:
                        refs = np.cumsum(np.fromiter(wy.refs, np.int64, len(wy.refs)))
                    art = wegart(t)
                    hw_ways.append((refs, art, belag(t, art)))
                if any(k in t for k in AREA_KEYS):
                    cat = gebiet(t)
                    if cat:
                        if refs is None:
                            refs = np.cumsum(np.fromiter(wy.refs, np.int64, len(wy.refs)))
                        if len(refs) >= 4 and refs[0] == refs[-1]:
                            area_ways.append((cat, [refs]))
    nid = np.concatenate(nid); nlat = np.concatenate(nlat); nlon = np.concatenate(nlon)
    o = np.argsort(nid); nid, nlat, nlon = nid[o], nlat[o], nlon[o]
    log(f"{len(nid):,} Knoten, {len(hw_ways):,} Wege, {len(area_ways):,} Flächen")

    def coords(refs):
        i = np.searchsorted(nid, refs)
        i[i >= len(nid)] = 0
        ok = nid[i] == refs
        if not ok.all():
            return None
        return np.column_stack([nlon[i] * KX, nlat[i] * KY])

    # --- OSM-Wege verdichten und indexieren
    W_IDX = {k: i for i, (k, _) in enumerate(WEGART)}
    B_IDX = {k: i for i, (k, _) in enumerate(BELAG)}
    P, PA, PB = [], [], []
    for refs, art, bel in hw_ways:
        i = np.searchsorted(nid, refs)
        i[i >= len(nid)] = 0
        ok = nid[i] == refs
        if ok.sum() < 2:
            continue
        xy = np.column_stack([nlon[i[ok]] * KX, nlat[i[ok]] * KY])
        pts, _ = densify(xy, 2.0)
        pts = np.vstack([pts, xy[-1:]])
        P.append(pts)
        PA.append(np.full(len(pts), W_IDX[art], np.int8))
        PB.append(np.full(len(pts), B_IDX[bel], np.int8))
    P = np.vstack(P); PA = np.concatenate(PA); PB = np.concatenate(PB)
    tree = cKDTree(P)
    log(f"Wegenetz: {len(P):,} Stützpunkte")

    # --- Flächen zusammensetzen
    for cat, mem in rels:
        outer = [member_refs[i].tolist() for i, role in mem if role in ("outer", "") and i in member_refs]
        inner = [member_refs[i].tolist() for i, role in mem if role == "inner" and i in member_refs]
        area_ways.append((cat, [np.array(r) for r in assemble_rings(outer)], [np.array(r) for r in assemble_rings(inner)]))
    polys = []  # (prio, cat, bbox, [outer paths], [inner paths])
    for item in area_ways:
        cat, outer = item[0], item[1]
        inner = item[2] if len(item) > 2 else []
        op = [c for c in (coords(r) for r in outer) if c is not None]
        if not op:
            continue
        ip = [c for c in (coords(r) for r in inner) if c is not None]
        allc = np.vstack(op)
        polys.append((G_PRIO[cat], cat, allc.min(0), allc.max(0), [MPath(c) for c in op], [MPath(c) for c in ip]))
    polys.sort(key=lambda p: p[0])
    log(f"{len(polys):,} Gebietsflächen")

    # --- Spaziergänge abtasten
    SX, SW, SLEN, SLINE = [], [], [], []
    for li, (wi, ll) in enumerate(lines):
        xy = np.column_stack([ll[:, 1] * KX, ll[:, 0] * KY])
        if len(xy) < 2:
            continue
        pts, ln = densify(xy, 2.0)
        SX.append(pts); SLEN.append(ln)
        SW.append(np.full(len(pts), wi, np.int32)); SLINE.append(np.full(len(pts), li, np.int32))
    SX = np.vstack(SX); SLEN = np.concatenate(SLEN); SW = np.concatenate(SW); SLINE = np.concatenate(SLINE)
    log(f"{len(SX):,} Abtastpunkte ({SLEN.sum() / 1000:,.0f} km)")

    d, idx = tree.query(SX, distance_upper_bound=a.match)
    hit = np.isfinite(d)
    s_art = np.full(len(SX), W_IDX["keiner"], np.int8)
    s_bel = np.full(len(SX), B_IDX["unbekannt"], np.int8)
    s_art[hit] = PA[idx[hit]]
    s_bel[hit] = PB[idx[hit]]
    log(f"Wegart zugeordnet ({hit.mean():.1%} mit OSM-Weg ≤ {a.match:g} m)")

    G_IDX = {k: i for i, (k, _) in enumerate(GEBIET)}
    s_geb = np.full(len(SX), G_IDX["sonstiges"], np.int8)
    done = np.zeros(len(SX), bool)
    order = np.argsort(SX[:, 0])
    sx_sorted = SX[order, 0]
    for prio, cat, lo, hi, op, ip in polys:
        a0, a1 = np.searchsorted(sx_sorted, [lo[0], hi[0]])
        if a0 == a1:
            continue
        cand = order[a0:a1]
        cand = cand[(~done[cand]) & (SX[cand, 1] >= lo[1]) & (SX[cand, 1] <= hi[1])]
        if not len(cand):
            continue
        q = SX[cand]
        inside = np.zeros(len(cand), bool)
        for p in op:
            inside ^= p.contains_points(q)
        for p in ip:
            inside &= ~p.contains_points(q)
        sel = cand[inside]
        s_geb[sel] = G_IDX[cat]
        done[sel] = True
    log("Gebiete zugeordnet")

    # --- Glätten: Stücke unter 10 m (z. B. Straßenquerungen) übernehmen die Kategorie davor
    def smooth(arr, min_n=5):
        arr = arr.copy()
        brk = np.flatnonzero((np.diff(arr) != 0) | (np.diff(SLINE) != 0)) + 1
        starts = np.concatenate([[0], brk]); ends = np.concatenate([brk, [len(arr)]])
        for k in range(len(starts)):
            s0, e0 = starts[k], ends[k]
            if e0 - s0 >= min_n:
                continue
            if k > 0 and SLINE[s0 - 1] == SLINE[s0]:
                arr[s0:e0] = arr[s0 - 1]
            elif k + 1 < len(starts) and SLINE[e0 - 1] == SLINE[min(e0, len(arr) - 1)]:
                arr[s0:e0] = arr[e0]
        return arr
    s_art, s_geb, s_bel = smooth(s_art), smooth(s_geb), smooth(s_bel)

    # --- Auswertung
    # --- Wiederholungen erkennen (gleiche Regel wie build.py: ≤ rep m neben
    #     schon gegangenem Weg, chronologisch; am selben Tag zählt ein Punkt
    #     erst als gegangen, wenn der Weg seitdem ≥ 2·tol länger ist als die
    #     Luftlinie, also nach einer Umkehr wie in einer Sackgasse)
    log("erkenne Wiederholungen …")
    tol = float(data.get("rep", {}).get("tol", 3.0))
    near = 2 * tol
    is_new = np.zeros(len(SX), bool)
    grid = {}
    nx, ny, nw, nc = [], [], [], []
    xs, ys, ws, ls = SX[:, 0].tolist(), SX[:, 1].tolist(), SW.tolist(), SLEN.tolist()
    cum, cur_w = 0.0, -1
    for i in range(len(xs)):
        x, y, wi = xs[i], ys[i], ws[i]
        if wi != cur_w:
            cur_w, cum = wi, 0.0
        cum += ls[i]
        cx, cy = int(x // tol), int(y // tol)
        found = False
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for m in grid.get((gx, gy), ()):
                    dd = (nx[m] - x) ** 2 + (ny[m] - y) ** 2
                    if dd > tol * tol:
                        continue
                    if nw[m] == wi and cum - nc[m] - math.sqrt(dd) < near:
                        continue
                    found = True
                    break
                if found:
                    break
            if found:
                break
        if not found:
            is_new[i] = True
            m = len(nx)
            nx.append(x); ny.append(y); nw.append(wi); nc.append(cum)
            grid.setdefault((cx, cy), []).append(m)
    log(f"{SLEN[is_new].sum() / 1000:,.0f} km ohne Wiederholung")

    def sums(cats, arr, mask=None, target=None):
        m = np.ones(len(arr), bool) if mask is None else mask
        v = np.bincount(arr[m], weights=SLEN[m], minlength=len(cats)) / 1000
        # auf die km aus data.json skalieren, damit sich alles exakt aufsummiert
        if target is not None and v.sum() > 0:
            v = v * (target / v.sum())
        return {k: round(float(x), 3) for (k, _), x in zip(cats, v) if x >= 0.0005}

    per_walk = []
    bounds = np.searchsorted(SW, np.arange(len(walks) + 1))  # SW ist aufsteigend
    tot = {k: defaultdict(float) for k in ("w", "g", "b", "wn", "gn", "bn")}
    for wi in range(len(walks)):
        wk = walks[wi]
        m = np.zeros(len(SX), bool); m[bounds[wi]:bounds[wi + 1]] = True
        mn = m & is_new
        new_km = wk.get("new")
        o = {"d": wk["d"], "km": wk["km"]}
        for key, cats, arr in (("w", WEGART, s_art), ("g", GEBIET, s_geb), ("b", BELAG, s_bel)):
            o[key] = sums(cats, arr, m, wk["km"])
            o[key + "n"] = sums(cats, arr, mn, new_km) if new_km else {}
        for key in tot:
            for c, v in o[key].items():
                tot[key][c] += v
        per_walk.append(o)
    totals = {k: {c: round(v, 2) for c, v in d.items()} for k, d in tot.items()}

    def runs_to_lines(arr):
        out = defaultdict(list)
        # Wechsel der Kategorie oder der Linie trennt
        brk = np.flatnonzero((np.diff(arr) != 0) | (np.diff(SLINE) != 0)) + 1
        starts = np.concatenate([[0], brk]); ends = np.concatenate([brk, [len(arr)]])
        for s0, e0 in zip(starts, ends):
            if SLINE[s0] != SLINE[e0 - 1]:
                continue
            seg = SX[s0:e0 + (1 if e0 < len(arr) and SLINE[e0] == SLINE[s0] else 0)]
            if len(seg) < 2:
                continue
            ll = simplify([(y / KY, x / KX) for x, y in seg], 3.0)
            out[int(arr[s0])].append(encode(ll))
        return out

    wl, gl = runs_to_lines(s_art), runs_to_lines(s_geb)
    res = {
        "generated": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "source": Path(a.pbf).name,
        "match_m": a.match,
        "labels": {"w": dict(WEGART), "g": dict(GEBIET), "b": dict(BELAG)},
        "total": totals,
        "walks": per_walk,
        "lines": {"w": {WEGART[k][0]: v for k, v in sorted(wl.items())},
                  "g": {GEBIET[k][0]: v for k, v in sorted(gl.items())}},
    }
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"→ {a.out} ({Path(a.out).stat().st_size / 1024:,.0f} KB)")
    for key, cats in (("w", WEGART), ("g", GEBIET), ("b", BELAG)):
        print("  " + ", ".join(f"{lab}: {res['total'][key].get(k, 0):,.0f}/{res['total'][key + 'n'].get(k, 0):,.0f} km" for k, lab in cats), file=sys.stderr)


if __name__ == "__main__":
    main()
