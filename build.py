#!/usr/bin/env python3
"""
Walking Munich – Build-Skript

Liest alle *.gpx-Dateien aus einem Ordner (Standard: ../Walking relativ zu
diesem Skript), fasst die Track-Segmente pro Tag zu Spaziergängen zusammen,
vereinfacht die Linien und schreibt eine kompakte data.json für index.html.

Nur Python-Standardbibliothek (ab Python 3.9).

Aufruf:
    python build.py                      # liest ../Walking, schreibt ./data.json
    python build.py PFAD/ZU/GPX -o data.json --tol 3
"""
import argparse
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Berlin")
except Exception:  # zoneinfo/tzdata fehlt (z. B. Windows ohne tzdata)
    TZ = None

R = 6371008.8


def local(dt):
    if TZ is not None:
        return dt.astimezone(TZ)
    # Fallback: grobe MEZ/MESZ-Regel
    y = dt.year
    def last_sun(m):
        d = datetime(y, m, 31, 1, tzinfo=timezone.utc)
        return d.replace(day=31 - (d.weekday() + 1) % 7)
    off = 2 if last_sun(3) <= dt < last_sun(10) else 1
    from datetime import timedelta
    return (dt + timedelta(hours=off)).replace(tzinfo=None)


def tag(el):
    return el.tag.rsplit("}", 1)[-1]


def parse_time(s):
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.fromisoformat(s[:19]).replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def simplify(pts, tol):
    """Douglas-Peucker in lokaler Meter-Projektion (iterativ)."""
    if len(pts) < 3 or tol <= 0:
        return pts
    k = math.cos(math.radians(pts[0][0]))
    xy = [(p[1] * k * 111320.0, p[0] * 110540.0) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = xy[i]
        bx, by = xy[j]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        best, idx = -1.0, -1
        for m in range(i + 1, j):
            px, py = xy[m]
            if L2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                d = math.hypot(px - ax - t * dx, py - ay - t * dy)
            if d > best:
                best, idx = d, m
        if best > tol:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, f in zip(pts, keep) if f]


def encode(pts, prec=5):
    """Google Encoded Polyline."""
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


def read_segments(path):
    """Liefert (name, [(lat, lon, time|None), ...]) je trkseg/rte."""
    root = ET.parse(path).getroot()
    for trk in root.iter():
        t = tag(trk)
        if t not in ("trk", "rte"):
            continue
        name = None
        for c in trk:
            if tag(c) == "name" and c.text:
                name = c.text.strip()
        groups = [c for c in trk if tag(c) == "trkseg"] if t == "trk" else [trk]
        for seg in groups:
            pts = []
            for p in seg:
                if tag(p) not in ("trkpt", "rtept"):
                    continue
                tm = None
                for c in p:
                    if tag(c) == "time" and c.text:
                        try:
                            tm = parse_time(c.text)
                        except ValueError:
                            pass
                pts.append((float(p.get("lat")), float(p.get("lon")), tm))
            if len(pts) >= 2:
                yield name, pts


def repeats(order, day_pts, tol=3.0, step=2.0):
    """Wiederholte Begehungen erkennen.

    Läuft die Tage chronologisch durch und tastet jeden Weg alle `step` Meter
    ab. Ein Abtastpunkt, der höchstens `tol` Meter neben einem schon gegangenen
    Weg liegt, gilt als Wiederholung – auch am selben Tag, z. B. in eine
    Sackgasse hinein und wieder heraus.

    Damit die eigene Spur der letzten Meter nicht als Wiederholung zählt (die
    Abtastpunkte liegen nur `step` m auseinander), wird ein Punkt desselben
    Tags nur dann als „schon gegangen“ gewertet, wenn der Weg seitdem deutlich
    länger ist als die Luftlinie dorthin (Umkehr, Runde). Beim Geradeaus- oder
    Um-die-Ecke-Gehen sind beide fast gleich. An einem Wendepunkt bleiben so
    nur etwa `tol` m unerkannt.

    Jede Begehung zählt, also auch mehrere am selben Tag. Liefert pro Tag die
    neuen Kilometer, die Netzlänge ohne Wiederholungen (jedes Wegstück nur
    einmal) und Linien der mehrfach gegangenen Abschnitte, gruppiert nach
    Anzahl der Begehungen.
    """
    k = math.cos(math.radians(48.14))
    KX, KY = k * 111320.0, 110574.0
    near = 2 * tol           # Umweg gegenüber Luftlinie, ab dem es eine Umkehr ist
    again = 2 * tol + step   # so weit gegangen ohne Berührung = neue Begehung
    grid = {}
    nx, ny, nday, ncum, cnt, last, lastc, nl = [], [], [], [], [], [], [], []
    gcum = 0.0         # über alle Tage durchlaufende Wegstrecke
    runs = []          # Folgen neu angelegter Knoten (für die Karte)
    new_km = {}
    for di, d in enumerate(order):
        new_m, cum = 0.0, 0.0
        for pts in day_pts[d]:
            xy = [(p[1] * KX, p[0] * KY) for p in pts]
            run = []
            for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
                L = math.hypot(x2 - x1, y2 - y1)
                n = max(1, int(L / step))
                for kk in range(n):
                    t = kk / n
                    x, y, l = x1 + t * (x2 - x1), y1 + t * (y2 - y1), L / n
                    cum += l
                    gcum += l
                    cx, cy = int(x // tol), int(y // tol)
                    best, bd = -1, tol * tol
                    for i in (cx - 1, cx, cx + 1):
                        for j in (cy - 1, cy, cy + 1):
                            for m in grid.get((i, j), ()):
                                dd = (nx[m] - x) ** 2 + (ny[m] - y) ** 2
                                if dd > bd:
                                    continue
                                # eigene frische Spur (Weg ≈ Luftlinie) überspringen
                                if nday[m] == di and cum - ncum[m] - math.sqrt(dd) < near:
                                    continue
                                best, bd = m, dd
                    if best >= 0:
                        # neue Begehung: anderer Tag oder seit der letzten
                        # Berührung mehr als `again` m gegangen (Sackgasse
                        # hin und zurück, Runde zweimal …); Berührungen
                        # direkt hintereinander gehören zur selben Begehung
                        if last[best] != di or gcum - lastc[best] > again:
                            cnt[best] += 1
                        last[best] = di
                        lastc[best] = gcum
                        if len(run) > 1:
                            runs.append(run)
                        run = []
                    else:
                        new_m += l
                        m = len(nx)
                        nx.append(x); ny.append(y); nday.append(di); ncum.append(cum)
                        cnt.append(1); last.append(di); lastc.append(gcum); nl.append(l)
                        grid.setdefault((cx, cy), []).append(m)
                        run.append(m)
            if len(run) > 1:
                runs.append(run)
        new_km[d] = new_m / 1000
    # Linien der mehrfach gegangenen Abschnitte, getrennt nach Häufigkeit
    freq = defaultdict(list)
    for run in runs:
        cur, seg = None, []
        for m in run:
            c = min(cnt[m], 5)
            if c != cur and seg:
                if cur > 1 and len(seg) > 1:
                    freq[cur].append(seg)
                seg = seg[-1:]
            cur = c
            seg.append((ny[m] / KY, nx[m] / KX))
        if cur and cur > 1 and len(seg) > 1:
            freq[cur].append(seg)
    km_by = defaultdict(float)
    for c, l in zip(cnt, nl):
        km_by[c] += l / 1000
    unique = sum(new_km.values())
    return new_km, unique, freq, dict(km_by)


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", nargs="?", default=str(here.parent / "Walking"), help="Ordner mit GPX-Dateien")
    ap.add_argument("-o", "--out", default=str(here / "data.json"))
    ap.add_argument("--tol", type=float, default=3.0, help="Vereinfachungs-Toleranz in Metern (Standard 3)")
    ap.add_argument("--rep-tol", type=float, default=0.5,
                    help="Vereinfachung der Wie-oft-Linien in Metern (Standard 0,5) – klein halten, sonst schneiden sie Ecken ab und enden neben dem Weg")
    ap.add_argument("--rep", type=float, default=3.0, help="Abstand in Metern, ab dem ein Weg als erneut gegangen gilt (Standard 3)")
    a = ap.parse_args()

    src = Path(a.src)
    files = sorted(src.rglob("*.gpx")) if src.is_dir() else [src]
    if not files:
        sys.exit(f"Keine GPX-Dateien in {src} gefunden.")

    # 1) Segmente einlesen, Duplikate verwerfen
    seen, raw = set(), []
    n_in = n_dup = 0
    for f in files:
        print(f"lese {f.name} …", file=sys.stderr)
        for name, pts in read_segments(f):
            n_in += 1
            h = hashlib.md5("".join(f"{p[0]:.6f},{p[1]:.6f};" for p in pts).encode()).hexdigest()
            if h in seen:
                n_dup += 1
                continue
            seen.add(h)
            raw.append(pts)

    # 2) Segmente mit Zeitstempeln nach Tag aufteilen. Punkte ohne Zeit (in
    #    JOSM eingefügt) erben den Tag des vorherigen Punkts mit Zeit, am
    #    Anfang den des ersten. Segmente, die in JOSM aus Aufzeichnungen
    #    mehrerer Tage zusammengefügt wurden, werden so wieder getrennt.
    pieces, untimed = [], []
    for pts in raw:
        days = [local(p[2]).strftime("%Y-%m-%d") if p[2] else None for p in pts]
        if not any(days):
            untimed.append(pts)
            continue
        cur = next(d for d in days if d)
        lab = []
        for d in days:
            cur = d or cur
            lab.append(cur)
        # Läufe bilden; kurze fremde Einsprengsel (< 5 Punkte, z. B. in JOSM
        # mit einem anderen Weg verschmolzene Knoten) dem Umfeld zuschlagen
        runs = []
        for i, d in enumerate(lab):
            if runs and runs[-1][0] == d:
                runs[-1][2] = i + 1
            else:
                runs.append([d, i, i + 1])
        for k, (d, i, j) in enumerate(runs):
            if j - i < 5 and len(runs) > 1:
                nb = [runs[x][0] for x in (k - 1, k + 1) if 0 <= x < len(runs) and runs[x][2] - runs[x][1] >= 5]
                if nb and all(n == nb[0] for n in nb):
                    for x in range(i, j):
                        lab[x] = nb[0]
        run_pts, run_day = [], lab[0]
        for p, d in zip(pts, lab):
            if d != run_day:
                if len(run_pts) >= 2:
                    pieces.append({"day": run_day, "pts": run_pts})
                run_pts = []
            run_day = d
            run_pts.append(p)
        if len(run_pts) >= 2:
            pieces.append({"day": run_day, "pts": run_pts})
    n_split = sum(1 for _ in pieces) - (len(raw) - len(untimed))

    # 3) Segmente ganz ohne Zeit dem Tag zuordnen, an dessen Weg sie
    #    anschließen (Endpunkt <= MATCH m von einem Endpunkt, sonst von
    #    irgendeinem Punkt eines datierten Wegs). Mehrere Durchläufe, damit
    #    auch Ketten aus mehreren nachgetragenen Stücken zugeordnet werden.
    MATCH = 60.0
    CELL = 0.001  # ~ 110 m x 75 m
    def cell(p):
        return (int(p[0] / CELL), int(p[1] / CELL))
    ends_idx, pts_idx = defaultdict(list), defaultdict(list)
    def index(piece):
        P = piece["pts"]
        for p in (P[0], P[-1]):
            ends_idx[cell(p)].append((p, piece["day"]))
        for p in P:
            pts_idx[cell(p)].append((p, piece["day"]))
    def lookup(idx, q):
        cx, cy = cell(q)
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for p, day in idx.get((cx + dx, cy + dy), ()):
                    d = hav(q, p)
                    if d <= MATCH and (best is None or d < best[0]):
                        best = (d, day)
        return best
    for pc in pieces:
        index(pc)
    todo, n_matched = untimed, 0
    while todo:
        rest = []
        for pts in todo:
            hits = [h for q in (pts[0], pts[-1]) for h in (lookup(ends_idx, q),) if h]
            if not hits:
                hits = [h for q in (pts[0], pts[-1]) for h in (lookup(pts_idx, q),) if h]
            if hits:
                pc = {"day": min(hits)[1], "pts": pts, "added": True}
                pieces.append(pc)
                index(pc)
                n_matched += 1
            else:
                rest.append(pts)
        if len(rest) == len(todo):
            break
        todo = rest
    for pts in todo:
        pieces.append({"day": "undatiert", "pts": pts, "added": True})

    # 4) Wiederholungen auswerten
    print("werte Wiederholungen aus …", file=sys.stderr)
    day_pts = defaultdict(list)
    for pc in pieces:
        day_pts[pc["day"]].append(pc["pts"])
    order = sorted(d for d in day_pts if d != "undatiert") + (["undatiert"] if "undatiert" in day_pts else [])
    new_km, unique_km, freq, km_by = repeats(order, day_pts, a.rep)

    # 5) Pro Tag zusammenfassen
    walks = defaultdict(lambda: {"segs": [], "km": 0.0, "km_add": 0.0, "sec": 0, "start": None})
    n_pts = 0
    for pc in pieces:
        P, w = pc["pts"], walks[pc["day"]]
        ll = [(p[0], p[1]) for p in P]
        km = sum(hav(ll[i], ll[i + 1]) for i in range(len(ll) - 1)) / 1000
        own = sorted(p[2] for p in P if p[2] and local(p[2]).strftime("%Y-%m-%d") == pc["day"])
        t0 = own[0] if own else None
        # Gehzeit = Summe der Lücken < 15 min (längere = Pause)
        w["sec"] += sum(d for d in ((own[i + 1] - own[i]).total_seconds() for i in range(len(own) - 1)) if 0 < d < 900)
        if t0 and (w["start"] is None or t0 < w["start"]):
            w["start"] = t0
        simp = simplify(ll, a.tol)
        n_pts += len(simp)
        w["segs"].append((t0, encode(simp)))
        w["km"] += km
        if pc.get("added"):
            w["km_add"] += km

    out = []
    for key in sorted(walks, key=lambda k: (k == "undatiert", k)):
        w = walks[key]
        w["segs"].sort(key=lambda s: (s[0] is None, s[0] or 0))
        out.append({
            "d": None if key == "undatiert" else key,
            "t": local(w["start"]).strftime("%H:%M") if w["start"] else None,
            "km": round(w["km"], 2),
            "min": round(w["sec"] / 60) if w["sec"] else None,
            "n": len(w["segs"]),
            "add": round(w["km_add"], 2) or None,
            "new": round(new_km.get(key, 0), 2),
            "p": [s[1] for s in w["segs"]],
        })

    data = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "files": [f.name for f in files],
        "walks": out,
        "rep": {
            "tol": a.rep,
            "unique_km": round(unique_km, 1),
            "km_by_count": {str(c): round(v, 1) for c, v in sorted(km_by.items())},
            "lines": {str(c): [encode(simplify(l, min(a.tol, a.rep_tol))) for l in ls] for c, ls in sorted(freq.items())},
        },
    }
    Path(a.out).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    total = sum(w["km"] for w in out)
    und = next((w["km"] for w in out if w["d"] is None), 0)
    print(f"ohne Wiederholungen (≤ {a.rep:g} m): {unique_km:,.1f} km; nach Anzahl Begehungen: "
          + ", ".join(f"{c}×: {v:,.1f} km" for c, v in sorted(km_by.items())), file=sys.stderr)
    print(f"{n_split} Segmente nach Tagen getrennt, {n_matched} von {len(untimed)} Segmenten ohne Zeit "
          f"einem Tag zugeordnet, {len(todo)} bleiben ohne Datum ({und:.1f} km)", file=sys.stderr)
    print(f"{len(files)} Datei(en), {n_in} Segmente ({n_dup} Duplikate), "
          f"{len(out)} Spaziergänge, {total:,.0f} km, {n_pts:,} Punkte → {a.out} "
          f"({Path(a.out).stat().st_size/1024:,.0f} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
