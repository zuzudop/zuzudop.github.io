#!/usr/bin/env python3
"""
Walking München – OSM-Auswertung

Ordnet jeden gegangenen Meter einer Wegart (Gehweg, Pfad, Straße …), einem
Belag und einem Gebiet (Friedhof, Spielplatz, Park …) aus OpenStreetMap zu.
Wertet außerdem die Stadtbezirke (admin_level 9) und Bezirksteile (10) aus:
gegangene km und Abdeckung des Wegenetzes je Bezirk.

Eingaben:  data.json (von build.py) und ein OSM-Auszug als .osm.pbf
Ausgabe:   osm.json und grenze.json (werden von index.html geladen)

Benötigt:  pip install numpy scipy matplotlib protobuf
Aufruf:    python osm_analyse.py Muenchen.osm.pbf
"""
import argparse
import json
import math
import os
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
    ("keiner", "Kein Weg in OSM"),
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
    ("sonstiges", "Ohne Flächenangabe"),
]
BELAG = [("fest", "Befestigt"), ("lose", "Unbefestigt"), ("unbekannt", "Unbekannt")]
BESOND = [  # Eigenschaften des Weges selbst, quer zu Wegart und Gebiet (Reihenfolge = Vorrang)
    ("bahnsteig", "Bahnsteig"),
    ("unterirdisch", "Tunnel/unterirdisch"),
    ("innen", "Innenraum/Passage"),
    ("bruecke", "Brücke/Steg"),
    ("querung", "Straßenquerung"),
    ("zugang", "Eingeschränkter Zugang"),
    ("keine", "Ohne Besonderheit"),
]


def _ebene(v):
    """Kleinste Zahl aus layer/level ("-1", "-2;-1", "0.5") oder None."""
    try:
        return min(float(x) for x in v.replace(",", ";").split(";") if x.strip())
    except (ValueError, AttributeError):
        return None


def besonderheit(t):
    if t.get("public_transport") == "platform" or t.get("railway") == "platform":
        return "bahnsteig"
    lay, lev = _ebene(t.get("layer")), _ebene(t.get("level"))
    if t.get("tunnel") in ("yes", "culvert") or t.get("location") == "underground" or \
            (lev is not None and lev < 0) or (lay is not None and lay < 0 and not t.get("bridge")):
        return "unterirdisch"
    if t.get("indoor") in ("yes", "corridor") or t.get("tunnel") == "building_passage" or \
            t.get("covered") in ("yes", "arcade", "colonnade"):
        return "innen"
    if t.get("bridge") not in (None, "no"):
        return "bruecke"
    if t.get("footway") == "crossing" or t.get("cycleway") == "crossing" or t.get("path") == "crossing":
        return "querung"
    if t.get("access") in ("customers", "destination", "permit", "delivery", "agricultural", "forestry") or \
            t.get("foot") in ("customers", "destination", "permit"):
        return "zugang"
    return "keine"
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
# Für die Abdeckung je Bezirk nicht mitgezählt (zu Fuß nicht begehbar bzw. kein echter Weg)
NO_FOOT_HW = {"motorway", "motorway_link", "trunk", "trunk_link"}
NO_FOOT_SERVICE = {"parking_aisle", "driveway", "drive-through"}


def begehbar(t):
    hw = t.get("highway")
    if hw in NO_FOOT_HW or t.get("service") in NO_FOOT_SERVICE or t.get("foot") == "no":
        return False
    if t.get("access") in ("private", "no") and t.get("foot") not in ("yes", "designated", "permissive"):
        return False
    return True


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


def write_json(path, obj):
    """Über eine Zwischendatei schreiben und ersetzen; unter Windows kann die
    Zieldatei kurz gesperrt sein (z. B. während ein Webserver sie ausliefert)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for versuch in range(20):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            time.sleep(0.5)
    sys.exit(f"{path.name} ist gesperrt – Ergebnis liegt in {tmp.name}")


def ring_area(xy):
    """Fläche eines Rings in m² (Gauß'sche Trapezformel)."""
    x, y = xy[:, 0], xy[:, 1]
    return abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2


def ring_centroid(xy):
    x, y = xy[:, 0], xy[:, 1]
    c = x * np.roll(y, -1) - np.roll(x, -1) * y
    A = c.sum() / 2
    if abs(A) < 1e-9:
        return xy.mean(0)
    return np.array([((x + np.roll(x, -1)) * c).sum() / (6 * A), ((y + np.roll(y, -1)) * c).sum() / (6 * A)])


def assign(pts, polys):
    """Index des ersten Polygons, in dem jeder Punkt liegt (-1 = keins)."""
    res = np.full(len(pts), -1, np.int32)
    order = np.argsort(pts[:, 0])
    xs = pts[order, 0]
    for k, p in enumerate(polys):
        a0, a1 = np.searchsorted(xs, [p["lo"][0], p["hi"][0]])
        cand = order[a0:a1]
        cand = cand[(res[cand] < 0) & (pts[cand, 1] >= p["lo"][1]) & (pts[cand, 1] <= p["hi"][1])]
        if not len(cand):
            continue
        q = pts[cand]
        inside = np.zeros(len(cand), bool)
        for mp in p["op"]:
            inside ^= mp.contains_points(q)
        for mp in p["ip"]:
            inside &= ~mp.contains_points(q)
        res[cand[inside]] = k
    return res


LUECKE_KATS = (("g", GEBIET, "g"), ("w", WEGART, "a"), ("b", BELAG, "b"), ("x", BESOND, "x"))   # Aufteilung nach Gebiet/Wegart/Belag/Besonderheit


def luecken_auswertung(NET, in_city, SX, match, log):
    """Begehbare OSM-Wege in der Stadt, an denen kein Spaziergang ≤ match m
    vorbeiführt („Abweichung zu OSM“): Linien und km je Gebiet, Wegart, Belag und Besonderheit."""
    NP, NL, NW = NET["p"], NET["l"], NET["w"]
    dist, _ = cKDTree(SX).query(NP[in_city], distance_upper_bound=match)
    gap = np.zeros(len(NP), bool)
    gap[np.flatnonzero(in_city)[~np.isfinite(dist)]] = True
    out = {"match_m": match}
    fmt = lambda cats, v: {k: round(float(v[i]), 1) for i, (k, _) in enumerate(cats) if v[i] >= .05}
    for key, cats, col in LUECKE_KATS:
        C = NET[col]
        sums = lambda sel: np.bincount(C[sel], weights=NL[sel], minlength=len(cats)) / 1000
        netz, luecke = sums(in_city), sums(gap)
        # zusammenhängende Lücken je OSM-Weg und Kategorie als Linien (ab 10 m, kürzere sind meist Querungen)
        brk = np.flatnonzero((np.diff(gap) != 0) | (np.diff(NW) != 0) | (np.diff(C) != 0)) + 1
        starts = np.concatenate([[0], brk]); ends = np.concatenate([brk, [len(gap)]])
        lines, n_kurz = defaultdict(list), 0
        for s0, e0 in zip(starts, ends):
            if not gap[s0]:
                continue
            if NL[s0:e0].sum() < 10:
                n_kurz += 1
                continue
            e1 = e0 + 1 if e0 < len(gap) and NW[e0] == NW[s0] else e0   # bis zum nächsten Punkt verlängern
            lines[cats[int(C[s0])][0]].append(encode(simplify([(y / KY, x / KX) for x, y in NP[s0:e1]], 2.0)))
        out[key] = {"netz": fmt(cats, netz), "luecke": fmt(cats, luecke), "lines": dict(lines)}
        log(f"Abweichung zu OSM ({key}): {luecke.sum():,.0f} von {netz.sum():,.0f} km begehbarer Wege nicht ≤ {match:g} m "
            f"gegangen ({sum(map(len, lines.values())):,} Stücke, {n_kurz:,} unter 10 m weggelassen)")
    return out, gap


def luecken_je_gebiet(NET, in_city, gap, ps):
    """km begehbares Netz und nicht gegangene km je Bezirk, nach Gebiet, Wegart, Belag und Besonderheit."""
    NP, NL = NET["p"], NET["l"]
    pidx = np.flatnonzero(in_city)
    na = assign(NP[pidx], ps)
    m = na >= 0
    ii, kk = pidx[m], na[m].astype(np.int64)
    rows = [{} for _ in ps]
    G = gap[ii]
    for key, cats, col in LUECKE_KATS:
        C, nc = NET[col][ii], len(cats)
        def mat(sel):
            return np.bincount(kk[sel] * nc + C[sel], weights=NL[ii][sel], minlength=len(ps) * nc).reshape(len(ps), nc) / 1000
        M = {"netz": mat(np.ones(len(ii), bool)), "luecke": mat(G)}
        fmt = lambda row: {cats[j][0]: round(float(v), 2) for j, v in enumerate(row) if v >= .005}
        for k in range(len(ps)):
            rows[k][key] = {n: fmt(m[k]) for n, m in M.items()}
    return rows


def admin_polys(admin, member_refs, coords):
    """Verwaltungsgrenzen (Stadt, Stadtbezirke, Bezirksteile) als Flächen."""
    def build(b):
        outer = [member_refs[i].tolist() for i, role in b["mem"] if role in ("outer", "") and i in member_refs]
        inner = [member_refs[i].tolist() for i, role in b["mem"] if role == "inner" and i in member_refs]
        op = [coords(np.array(r)) for r in assemble_rings(outer)]
        ip = [coords(np.array(r)) for r in assemble_rings(inner)]
        if not op or any(c is None for c in op):
            return None
        ip = [c for c in ip if c is not None]
        allc = np.vstack(op)
        big = max(op, key=ring_area)
        return {**b, "op_xy": op, "op": [MPath(c) for c in op], "ip": [MPath(c) for c in ip],
                "lo": allc.min(0), "hi": allc.max(0), "c": ring_centroid(big),
                "area": sum(ring_area(c) for c in op) - sum(ring_area(c) for c in ip)}
    polys = [p for p in (build(b) for b in admin) if p]
    stadt = next((p for p in polys if p["level"] == 6), None)
    for p in polys:
        p["in_stadt"] = not stadt or any(mp.contains_point(p["c"]) for mp in stadt["op"])
    return polys, stadt


def stadtbezirke(polys):
    """Stadtbezirke (admin_level 9) in der Stadt, sortiert wie in osm.json."""
    ps = [p for p in polys if p["level"] == 9 and p["in_stadt"]]
    return sorted(ps, key=lambda p: (p["ref"].zfill(5), p["name"]))


def bezirks_auswertung(polys, stadt, a, SX, SLEN, SW, is_new, walks, NET, kats, log):
    """Stadtgrenze → grenze.json; je Stadtbezirk/Bezirksteil: Fläche, gegangene
    km, (anteilige) Gehzeit, erster Besuch und km je Wegart/Gebiet/Belag. Dazu
    die „Abweichung zu OSM“ (nicht gegangene begehbare Wege in der Stadt)."""
    def to_ll(xy, tol):
        return encode(simplify([(y / KY, x / KX) for x, y in xy], tol))

    if stadt:
        write_json(a.grenze, {"name": a.stadt, "rings": [to_ll(c, 5.0) for c in stadt["op_xy"]]})
        log(f"Stadtgrenze {a.stadt} → {Path(a.grenze).name}")
    else:
        log(f"Stadtgrenze {a.stadt} nicht gefunden")

    in_stadt = lambda p: p["in_stadt"]

    # Abweichung zu OSM: begehbare Wege in der Stadt (ohne Stadtgrenze: im ganzen Bereich)
    in_city = assign(NET["p"], [stadt]) >= 0 if stadt else np.ones(len(NET["p"]), bool)
    luecken, gap = luecken_auswertung(NET, in_city, SX, a.match, log)
    luecken["bezirke"] = {}

    if not any(p["level"] in (9, 10) for p in polys):
        return None, luecken

    dates = [w["d"] for w in walks]
    # Namen der Bezirksteile (von bezirksteil_namen.py), da OSM meist nur Nummern hat
    nf = Path(a.out).resolve().parent / "bezirksteile.json"
    teil_namen = json.loads(nf.read_text(encoding="utf-8")) if nf.exists() else {}
    for p in polys:
        if p["level"] == 10 and not p["name"]:
            p["name"] = teil_namen.get(p["ref"], "")
    out = {}
    for lvl in (9, 10):
        ps = [p for p in polys if p["level"] == lvl and in_stadt(p)]
        ps.sort(key=lambda p: (p["ref"].zfill(5), p["name"]))
        if not ps:
            continue
        luecken["bezirke"][str(lvl)] = luecken_je_gebiet(NET, in_city, gap, ps)   # gleiche Reihenfolge wie osm.json
        sa = assign(SX, ps)
        s = sa >= 0
        km = np.bincount(sa[s], weights=SLEN[s], minlength=len(ps)) / 1000
        sn = s & is_new
        kmn = np.bincount(sa[sn], weights=SLEN[sn], minlength=len(ps)) / 1000
        # km je (Bezirk, Spaziergang) → Anzahl Spaziergänge (≥ 100 m) und erster Besuch
        key = sa[s].astype(np.int64) * len(walks) + SW[s]
        per = np.bincount(key, weights=SLEN[s], minlength=len(ps) * len(walks)).reshape(len(ps), len(walks))
        # Gehzeit je Gebiet, anteilig: Minuten des Spaziergangs × Anteil seiner Strecke im Gebiet
        # (Zeitstempel liegen nur je Spaziergang vor, nicht je Punkt)
        walk_len = np.bincount(SW, weights=SLEN, minlength=len(walks))
        walk_min = np.array([w.get("min") or 0 for w in walks], float)
        minuten = per @ np.divide(walk_min, walk_len, out=np.zeros(len(walks)), where=walk_len > 0)
        # km je Kategorie (w/g/b) im Gebiet, mit und ohne Wiederholung (Suffix n)
        kat = [{} for _ in ps]
        for kk, cats, arr in kats:
            for mask, suf in ((s, ""), (sn, "n")):
                M = np.bincount(sa[mask].astype(np.int64) * len(cats) + arr[mask], weights=SLEN[mask],
                                minlength=len(ps) * len(cats)).reshape(len(ps), len(cats)) / 1000
                for k in range(len(ps)):
                    kat[k][kk + suf] = {cats[j][0]: round(float(v), 2) for j, v in enumerate(M[k]) if v >= 0.005}
        rows = []
        for k, p in enumerate(ps):
            vis = [wi for wi in np.flatnonzero(per[k] >= 100) if dates[wi]]
            rows.append({
                "name": p["name"], "ref": p["ref"], "wikidata": p["wikidata"],
                "km2": round(p["area"] / 1e6, 3),
                "km": round(float(km[k]), 2), "kmn": round(float(kmn[k]), 2),
                "min": round(float(minuten[k])),
                "n": len(vis), "first": min(dates[wi] for wi in vis) if vis else None,
                "rings": [to_ll(c, 5.0) for c in p["op_xy"]],
                **kat[k],
                "_c": p["c"],
            })
        out[str(lvl)] = rows
        log(f"admin_level {lvl}: {len(rows)} Gebiete")
    # Bezirksteil → Stadtbezirk (über den Schwerpunkt)
    if "9" in out and "10" in out:
        bz = stadtbezirke(polys)
        for r in out["10"]:
            r["bz"] = next((k for k, p in enumerate(bz) if any(mp.contains_point(r["_c"]) for mp in p["op"])), None)
    for rows in out.values():
        for r in rows:
            del r["_c"]
    return out, luecken


# ---------------------------------------------------------------- Hauptteil
def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pbf", help="OSM-Auszug (.osm.pbf), z. B. Muenchen.osm.pbf von BBBike")
    ap.add_argument("--data", default=str(here / "data.json"))
    ap.add_argument("-o", "--out", default=str(here / "osm.json"))
    ap.add_argument("--match", type=float, default=12.0, help="max. Abstand zu einem OSM-Weg in m (Standard 12)")
    ap.add_argument("--rand", type=float, default=20.0,
                    help="Wege ohne Fläche (z. B. Straßenraum) der nächsten Fläche bis zu diesem Abstand zuordnen, in m (Standard 20)")
    ap.add_argument("--stadt", default="München", help="Name der Stadt (Grenze admin_level 6, Standard München)")
    ap.add_argument("--grenze", default=str(here / "grenze.json"), help="Ausgabe der Stadtgrenze")
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

    # --- Durchlauf 1: Relationen (Multipolygone, Verwaltungsgrenzen)
    rels, admin = [], []
    need_ways = set()
    for pb in osmpbf.blocks(a.pbf):
        st = pb.stringtable.s
        for g in pb.primitivegroup:
            for r in g.relations:
                t = {st[k].decode(): st[v].decode() for k, v in zip(r.keys, r.vals)}
                lvl = t.get("admin_level")
                if t.get("boundary") == "administrative" and (lvl in ("9", "10") or (lvl == "6" and t.get("name") == a.stadt)):
                    cat = None
                elif t.get("type") == "multipolygon":
                    cat = gebiet(t)
                    if not cat:
                        continue
                else:
                    continue
                ids = np.cumsum(np.fromiter(r.memids, np.int64, len(r.memids)))
                mem = [(int(i), st[rs].decode()) for i, rs, ty in zip(ids, r.roles_sid, r.types) if ty == 1]
                if cat:
                    rels.append((cat, mem))
                else:
                    admin.append({"level": int(lvl), "name": t.get("name", ""), "ref": t.get("ref", ""),
                                  "wikidata": t.get("wikidata", ""), "mem": mem})
                need_ways.update(i for i, _ in mem)
    log(f"{len(rels)} Gebiets-Relationen, {sum(b['level'] == 9 for b in admin)} Stadtbezirke, "
        f"{sum(b['level'] == 10 for b in admin)} Bezirksteile")

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
                    hw_ways.append((refs, art, belag(t, art), begehbar(t), besonderheit(t)))
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

    # --- Knoten der Stadtgrenze außerhalb des Spaziergang-Bereichs nachladen
    #     (nur falls nötig; Bezirke von Nachbarorten werden nicht gebraucht)
    adm_ids = [member_refs[i] for b in admin if b["level"] == 6 for i, _ in b["mem"] if i in member_refs]
    if adm_ids:
        adm_ids = np.unique(np.concatenate(adm_ids))
        miss = adm_ids[~np.isin(adm_ids, nid)]
        if len(miss):
            log(f"lade {len(miss):,} Grenzknoten außerhalb des Bereichs nach …")
            add = [(nid, nlat, nlon)]
            for pb in osmpbf.blocks(a.pbf):
                for g in pb.primitivegroup:
                    if g.HasField("dense") and len(g.dense.id):
                        ids, la, lo = osmpbf.dense_nodes(pb, g)
                        m = np.isin(ids, miss)
                        if m.any():
                            add.append((ids[m], la[m], lo[m]))
            nid, nlat, nlon = (np.concatenate(x) for x in zip(*add))
            o = np.argsort(nid); nid, nlat, nlon = nid[o], nlat[o], nlon[o]

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
    X_IDX = {k: i for i, (k, _) in enumerate(BESOND)}
    P, PA, PB, PX = [], [], [], []
    NP, NL, NA, NWY, NX, NB = [], [], [], [], [], []  # begehbares Wegenetz alle 5 m: Punkt, Länge, Wegart, Weg-Nr., Besonderheit, Belag
    for wno, (refs, art, bel, foot, bes) in enumerate(hw_ways):
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
        PX.append(np.full(len(pts), X_IDX[bes], np.int8))
        if foot:
            q, ql = densify(xy, 5.0)
            q = np.vstack([q, xy[-1:]]); ql = np.append(ql, 0.0)   # Endpunkt für durchgehende Linien
            NP.append(q); NL.append(ql)
            NA.append(np.full(len(q), W_IDX[art], np.int8)); NWY.append(np.full(len(q), wno, np.int32))
            NX.append(np.full(len(q), X_IDX[bes], np.int8)); NB.append(np.full(len(q), B_IDX[bel], np.int8))
    P = np.vstack(P); PA = np.concatenate(PA); PB = np.concatenate(PB); PX = np.concatenate(PX)
    NET = {"p": np.vstack(NP), "l": np.concatenate(NL), "a": np.concatenate(NA), "w": np.concatenate(NWY),
           "x": np.concatenate(NX), "b": np.concatenate(NB)}
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
    s_bes = np.full(len(SX), X_IDX["keine"], np.int8)
    s_bes[hit] = PX[idx[hit]]
    log(f"Wegart zugeordnet ({hit.mean():.1%} mit OSM-Weg ≤ {a.match:g} m)")

    G_IDX = {k: i for i, (k, _) in enumerate(GEBIET)}

    def gebiete(pts):
        """Flächenart je Punkt (bei Überlappung die speziellere, sonst „sonstiges“)."""
        res = np.full(len(pts), G_IDX["sonstiges"], np.int8)
        done = np.zeros(len(pts), bool)
        order = np.argsort(pts[:, 0])
        xs = pts[order, 0]
        for prio, cat, lo, hi, op, ip in polys:
            a0, a1 = np.searchsorted(xs, [lo[0], hi[0]])
            if a0 == a1:
                continue
            cand = order[a0:a1]
            cand = cand[(~done[cand]) & (pts[cand, 1] >= lo[1]) & (pts[cand, 1] <= hi[1])]
            if not len(cand):
                continue
            q = pts[cand]
            inside = np.zeros(len(cand), bool)
            for p in op:
                inside ^= p.contains_points(q)
            for p in ip:
                inside &= ~p.contains_points(q)
            sel = cand[inside]
            res[sel] = G_IDX[cat]
            done[sel] = True
        return res

    # „Ohne Flächenangabe“ verkleinern: OSM-Flächen enden meist am Straßenrand, der Straßenraum
    # liegt in keiner Fläche. Solche Punkte bekommen die nächste Fläche im Umkreis von --rand m.
    # Kommen mehrere Flächen infrage, gewinnt eine, deren Rand im selben Stadtbezirk liegt wie der Punkt.
    apolys, stadt = admin_polys(admin, member_refs, coords)
    BZ = stadtbezirke(apolys)
    BP, BC = [], []
    for prio, cat, lo, hi, op, ip in polys:
        for mp in op:
            q, _ = densify(mp.vertices, 5.0)
            BP.append(q); BC.append(np.full(len(q), G_IDX[cat], np.int8))
    BP = np.vstack(BP); BC = np.concatenate(BC)
    BB = assign(BP, BZ) if BZ else np.full(len(BP), -1, np.int32)   # Stadtbezirk jedes Randpunkts
    btree = cKDTree(BP)
    SONST = G_IDX["sonstiges"]

    def nachbarflaeche(pts, res, k=24):
        idx = np.flatnonzero(res == SONST)
        if not len(idx):
            return res
        q = pts[idx]
        qb = assign(q, BZ) if BZ else np.full(len(q), -1, np.int32)
        d, j = btree.query(q, k=k, distance_upper_bound=a.rand)   # nach Abstand sortiert
        ok = np.isfinite(d)
        jj = np.where(ok, j, 0)
        gleich = ok & (BB[jj] == qb[:, None]) & (qb[:, None] >= 0)
        hat_gleich, hat_irgend = gleich.any(1), ok[:, 0]
        wahl = np.where(hat_gleich, gleich.argmax(1), 0)          # nächster im eigenen Bezirk, sonst nächster
        sel = hat_gleich | hat_irgend
        out = res.copy()
        out[idx[sel]] = BC[jj[np.arange(len(idx)), wahl]][sel]
        return out

    s_geb0 = gebiete(SX)
    s_geb = nachbarflaeche(SX, s_geb0)
    NET["g"] = nachbarflaeche(NET["p"], gebiete(NET["p"]))   # für die Abweichung zu OSM nach Gebiet
    log(f"Gebiete zugeordnet (ohne Flächenangabe: {SLEN[s_geb0 == SONST].sum() / 1000:,.0f} → "
        f"{SLEN[s_geb == SONST].sum() / 1000:,.0f} km, Umkreis {a.rand:g} m)")

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
    s_art, s_geb, s_bel = smooth(s_art), smooth(s_geb), smooth(s_bel)   # s_bes nicht: Querungen sind kurz

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
    tot = {k: defaultdict(float) for k in ("w", "g", "b", "x", "wn", "gn", "bn", "xn")}
    for wi in range(len(walks)):
        wk = walks[wi]
        m = np.zeros(len(SX), bool); m[bounds[wi]:bounds[wi + 1]] = True
        mn = m & is_new
        new_km = wk.get("new")
        o = {"d": wk["d"], "km": wk["km"]}
        for key, cats, arr in (("w", WEGART, s_art), ("g", GEBIET, s_geb), ("b", BELAG, s_bel), ("x", BESOND, s_bes)):
            o[key] = sums(cats, arr, m, wk["km"])
            o[key + "n"] = sums(cats, arr, mn, new_km) if new_km else {}
        for key in tot:
            for c, v in o[key].items():
                tot[key][c] += v
        per_walk.append(o)
    totals = {k: {c: round(v, 2) for c, v in d.items()} for k, d in tot.items()}

    # --- Stadtgrenze und Bezirke
    kats = (("w", WEGART, s_art), ("g", GEBIET, s_geb), ("b", BELAG, s_bel), ("x", BESOND, s_bes))
    bezirke, luecken = bezirks_auswertung(apolys, stadt, a, SX, SLEN, SW, is_new, walks, NET, kats, log)
    if luecken:
        lf = Path(a.out).resolve().parent / "osm_luecken.json"
        write_json(lf, luecken)
        log(f"→ {lf.name} ({lf.stat().st_size / 1024:,.0f} KB)")

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

    wl, gl, bl, xl = runs_to_lines(s_art), runs_to_lines(s_geb), runs_to_lines(s_bel), runs_to_lines(s_bes)
    xl.pop(X_IDX["keine"], None)   # „ohne Besonderheit“ wäre fast alles – keine eigenen Linien
    res = {
        "generated": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "source": Path(a.pbf).name,
        "match_m": a.match,
        "labels": {"w": dict(WEGART), "g": dict(GEBIET), "b": dict(BELAG), "x": dict(BESOND)},
        "total": totals,
        "walks": per_walk,
        "lines": {"w": {WEGART[k][0]: v for k, v in sorted(wl.items())},
                  "g": {GEBIET[k][0]: v for k, v in sorted(gl.items())},
                  "b": {BELAG[k][0]: v for k, v in sorted(bl.items())},
                  "x": {BESOND[k][0]: v for k, v in sorted(xl.items())}},
        "bezirke": bezirke,
    }
    write_json(a.out, res)
    log(f"→ {a.out} ({Path(a.out).stat().st_size / 1024:,.0f} KB)")
    for key, cats in (("w", WEGART), ("g", GEBIET), ("b", BELAG), ("x", BESOND)):
        print("  " + ", ".join(f"{lab}: {res['total'][key].get(k, 0):,.0f}/{res['total'][key + 'n'].get(k, 0):,.0f} km" for k, lab in cats), file=sys.stderr)


if __name__ == "__main__":
    main()
