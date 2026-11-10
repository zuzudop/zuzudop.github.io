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


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", nargs="?", default=str(here.parent / "Walking"), help="Ordner mit GPX-Dateien")
    ap.add_argument("-o", "--out", default=str(here / "data.json"))
    ap.add_argument("--tol", type=float, default=3.0, help="Vereinfachungs-Toleranz in Metern (Standard 3)")
    a = ap.parse_args()

    src = Path(a.src)
    files = sorted(src.rglob("*.gpx")) if src.is_dir() else [src]
    if not files:
        sys.exit(f"Keine GPX-Dateien in {src} gefunden.")

    seen = set()
    walks = defaultdict(lambda: {"segs": [], "km": 0.0, "sec": 0, "start": None, "end": None, "names": set()})
    n_in = n_pts = n_dup = 0
    for f in files:
        print(f"lese {f.name} …", file=sys.stderr)
        for name, pts in read_segments(f):
            n_in += 1
            h = hashlib.md5("".join(f"{p[0]:.6f},{p[1]:.6f};" for p in pts).encode()).hexdigest()
            if h in seen:
                n_dup += 1
                continue
            seen.add(h)
            times = [p[2] for p in pts if p[2] is not None]
            if times:
                # Tag = häufigstes Datum der Punkte (robust gegen in JOSM
                # zusammengefügte Segmente mit Ausreißer-Zeitstempeln)
                days = Counter(local(t).strftime("%Y-%m-%d") for t in times)
                key = days.most_common(1)[0][0]
                own = sorted(t for t in times if local(t).strftime("%Y-%m-%d") == key)
                t0, t1 = own[0], own[-1]
                # Gehzeit = Summe der Lücken < 15 min (längere = Pause)
                sec = sum(d for d in ((own[i + 1] - own[i]).total_seconds() for i in range(len(own) - 1)) if 0 < d < 900)
            else:
                t0 = t1 = None
                sec = 0
                key = "undatiert"
            ll = [(p[0], p[1]) for p in pts]
            km = sum(hav(ll[i], ll[i + 1]) for i in range(len(ll) - 1)) / 1000
            w = walks[key]
            simp = simplify(ll, a.tol)
            n_pts += len(simp)
            w["segs"].append((t0, encode(simp)))
            w["km"] += km
            if t0:
                w["sec"] += sec
                w["start"] = t0 if w["start"] is None or t0 < w["start"] else w["start"]
                w["end"] = t1 if w["end"] is None or t1 > w["end"] else w["end"]
            if name and name != "Track":
                w["names"].add(name)

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
            "p": [s[1] for s in w["segs"]],
        })

    data = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "files": [f.name for f in files],
        "walks": out,
    }
    Path(a.out).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    total = sum(w["km"] for w in out)
    print(f"{len(files)} Datei(en), {n_in} Segmente ({n_dup} Duplikate), "
          f"{len(out)} Spaziergänge, {total:,.0f} km, {n_pts:,} Punkte → {a.out} "
          f"({Path(a.out).stat().st_size/1024:,.0f} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
