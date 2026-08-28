#!/usr/bin/env python3
"""
openplan3d-quilt-twin — project a floor plan onto the quilt substrate.

Reads either:
  * an Apple RoomPlan JSON export (identifier / parentIdentifier / 4x4
    column-major transforms, meters) — e.g. this repo's test-roomplan.json
  * an openPlan3D native Project JSON export (src/lib/models/types.ts)

Emits a quilt cell-graph document (format: quilt-cellgraph/1):
  nodes  = CELLS  (one per placed object; rooms are cells too)
  edges  = LINKS  (hosted-in, bounds, joined-at, placed-in, grouped, passable)
  views  = VIEW   (plan2d render, walk3d camera)
  ticks  = TICK   (version-history snapshot cadence, sim step)

Stdlib only. Python >= 3.9.
"""
import argparse
import json
import math
import sys
import time
import uuid
from collections import defaultdict

EPS_JOIN = 10.0        # cm — endpoints closer than this are "joined"
EPS_CONTAIN = 1.0      # cm — point-in-polygon tolerance
OPENING_PASS = True    # doors create passable edges between rooms

def cid(kind):
    return f"cell:{kind}:{uuid.uuid4().hex[:10]}"

# ---------------------------------------------------------------- geometry
def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])

def point_in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xint:
                inside = not inside
    return inside

def poly_area(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0

def poly_centroid(poly):
    if not poly:
        return [0.0, 0.0]
    n = len(poly)
    return [sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n]

# ---------------------------------------------------------------- builder
class Graph:
    def __init__(self):
        self.nodes = []
        self.edges = []
        self._by_id = {}
        self._n = 0
        self._e = 0

    def node(self, kind, position, points=None, story=0, attrs=None, telemetry=None):
        nid = cid(kind)
        nd = {
            "id": nid, "kind": kind, "story": story,
            "geometry": {"position": [round(position[0], 2), round(position[1], 2),
                                      round(position[2] if len(position) > 2 else 0.0, 2)]},
            "attrs": attrs or {},
            "telemetry": telemetry or {},
        }
        if points:
            nd["geometry"]["points"] = [[round(p[0], 2), round(p[1], 2)] for p in points]
        self.nodes.append(nd)
        self._by_id[nid] = nd
        self._n += 1
        return nid

    def edge(self, kind, src, dst, weight=None, attrs=None):
        self._e += 1
        e = {"id": f"e:{self._e:04d}", "kind": kind, "src": src, "dst": dst}
        if weight is not None:
            e["weight"] = round(weight, 2)
        if attrs:
            e.update(attrs)
        self.edges.append(e)
        return e

    def by_label(self, label):
        for nd in self.nodes:
            if nd["attrs"].get("label", "").lower() == label.lower():
                return nd
        return None

# ---------------------------------------------------------------- RoomPlan
def build_roomplan(data):
    """Apple RoomPlan JSON -> cell graph."""
    g = Graph()
    wall_ids = {}      # rp identifier -> cell id
    wall_segs = {}     # cell id -> (start, end)
    story_of = lambda it: it.get("story", 0) if isinstance(it.get("story"), int) else 0
    cat = lambda it: (it.get("category") or {})
    label_of = lambda it: cat(it).get("categoryIdentifier") or cat(it).get("identifier") or ""

    # sections first (rooms), keyed by story
    sec_cells = []
    for s in data.get("sections", []):
        c = s.get("center") or [0, 0, 0]
        nid = g.node("room", [c[0] * 100, c[2] * 100, 0.0],
                     attrs={"label": s.get("label") or "room",
                            "confidence": (s.get("confidence") or {}).get("confidence", 0.0)},
                     telemetry={"source": "roomplan-section"})
        sec_cells.append((nid, s, story_of(s)))

    # walls
    for w in data.get("walls", []):
        t = w.get("transform") or []
        dims = w.get("dimensions") or []
        if len(t) < 16 or len(dims) < 2:
            continue
        cx, cz = t[12] * 100, t[14] * 100
        half = (dims[0] / 2) * 100
        dx, dz = t[0], t[2]                      # local X axis in world XZ
        start, end = (cx - dx * half, cz - dz * half), (cx + dx * half, cz + dz * half)
        st = story_of(w)
        nid = g.node("wall", [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2,
                              (dims[1] * 100) / 2],
                     points=[start, end], story=st,
                     attrs={"label": "wall", "roomplan-category": label_of(w),
                            "origin": w.get("identifier")},
                     telemetry={"length": round(dist(start, end), 2),
                                "height": round(dims[1] * 100, 2)})
        wall_ids[w["identifier"]] = nid
        wall_segs[nid] = (start, end)

    # openings: doors + windows
    opening_cells = []
    for kind in ("doors", "windows"):
        for o in data.get(kind, []):
            t = o.get("transform") or []
            dims = o.get("dimensions") or []
            if len(t) < 16:
                continue
            st = story_of(o)
            nid = g.node("door" if kind == "doors" else "window",
                         [t[12] * 100, t[14] * 100, (dims[1] * 100) / 2 if len(dims) > 1 else 100.0],
                         story=st,
                         attrs={"label": ("door" if kind == "doors" else "window"),
                                "roomplan-category": label_of(o),
                                "origin": o.get("identifier")},
                         telemetry={"width": round((dims[0] if dims else 0) * 100, 2)})
            parent = o.get("parentIdentifier")
            if parent and parent in wall_ids:
                g.edge("hosted-in", nid, wall_ids[parent],
                       weight=dist((t[12] * 100, t[14] * 100),
                                   (g._by_id[wall_ids[parent]]["geometry"]["position"][0],
                                    g._by_id[wall_ids[parent]]["geometry"]["position"][1])))
            if kind == "doors":
                opening_cells.append(nid)

    # objects (furniture / fixtures)
    for o in data.get("objects", []):
        t = o.get("transform") or []
        if len(t) < 16:
            continue
        attrs = o.get("attributes") or {}
        g.node("furniture", [t[12] * 100, t[14] * 100, t[13] * 100],
               story=story_of(o),
               attrs={"label": attrs.get("canonicalName") or label_of(o) or "object",
                      "roomplan-category": label_of(o), "origin": o.get("identifier")},
               telemetry={"dimensions": [round(d * 100, 1) for d in (o.get("dimensions") or [])[:3]]})

    # floors
    for f in data.get("floors", []):
        poly = [(p["x"] * 100, p["z"] * 100) for p in (f.get("polygonCorners") or []) if "x" in p and "z" in p]
        if len(poly) < 3:
            continue
        g.node("floor", poly_centroid(poly), points=poly, story=story_of(f),
               attrs={"label": "floor", "origin": f.get("identifier")},
               telemetry={"area": round(poly_area(poly), 2)})

    _shared_derivation(g, sec_cells, wall_segs, opening_cells, EPS_JOIN)
    return g, "roomplan"

# ---------------------------------------------------------------- native Project
def build_project(data):
    """openPlan3D native Project JSON -> cell graph."""
    g = Graph()
    wall_segs = {}
    wall_cell_of = {}   # project wall id -> cell id
    sec_cells = []
    for floor in data.get("floors", []):
        st = floor.get("level", 0)
        for w in floor.get("walls", []):
            s, e = (w["start"]["x"], w["start"]["y"]), (w["end"]["x"], w["end"]["y"])
            nid = g.node("wall", [(s[0] + e[0]) / 2, (s[1] + e[1]) / 2, (w.get("height", 270)) / 2],
                         points=[s, e], story=st,
                         attrs={"label": "wall", "origin": w.get("id")},
                         telemetry={"length": round(dist(s, e), 2),
                                    "height": w.get("height", 270)})
            wall_segs[nid] = (s, e)
            if w.get("id") is not None:
                wall_cell_of[w["id"]] = nid
        for r in floor.get("rooms", []):
            pts = [wall_segs[wall_cell_of[wid]][0] for wid in r.get("walls", [])
                   if wid in wall_cell_of]
            dedup = []
            for p in pts:
                if not dedup or p != dedup[-1]:
                    dedup.append(p)
            poly = dedup if len(dedup) >= 3 else []
            nid = g.node("room", poly_centroid(poly) if poly else [0, 0],
                         points=poly if poly else None, story=st,
                         attrs={"label": r.get("name") or "room", "origin": r.get("id"),
                                "roomType": r.get("roomType", "indoor")},
                         telemetry={"area": round(r.get("area", poly_area(poly) if poly else 0), 2)})
            for wid in r.get("walls", []):
                if wid in wall_cell_of:
                    g.edge("bounds", nid, wall_cell_of[wid])
            sec_cells.append((nid, {"__floor": st}, st))
        # doors / windows -> hosted-in
        for kind in ("doors", "windows"):
            for o in floor.get(kind, []):
                host = wall_cell_of.get(o.get("wallId"))
                if host is None:
                    continue
                seg = wall_segs[host]
                p01 = o.get("position", 0.5)
                pos = (seg[0][0] + (seg[1][0] - seg[0][0]) * p01,
                       seg[0][1] + (seg[1][1] - seg[0][1]) * p01)
                nid = g.node("door" if kind == "doors" else "window",
                             [pos[0], pos[1], o.get("height", 200) / 2], story=st,
                             attrs={"label": kind[:-1], "origin": o.get("id"),
                                    "doorType": o.get("type")},
                             telemetry={"width": o.get("width", 90),
                                        "positionOnWall": p01})
                g.edge("hosted-in", nid, host, weight=0.0)
        for f in floor.get("furniture", []):
            p = f.get("position") or {"x": 0, "y": 0}
            g.node("furniture", [p["x"], p["y"], 0], story=st,
                   attrs={"label": f.get("catalogId", "furniture"), "origin": f.get("id")},
                   telemetry={"rotation": f.get("rotation", 0),
                              "scale": f.get("scale", {})})
        for grp in floor.get("groups", []):
            members = [n["id"] for n in g.nodes
                       if n["attrs"].get("origin") in grp.get("elementIds", [])]
            for a in members:
                for b in members:
                    if a < b:
                        g.edge("grouped", a, b)

    opening_cells = [n["id"] for n in g.nodes if n["kind"] == "door"]
    _shared_derivation(g, [(nid, s, st) for nid, s, st in sec_cells], wall_segs,
                       opening_cells, EPS_JOIN)
    return g, "project"

# ------------------------------------------------- shared derivation (edges)
def _shared_derivation(g, sec_cells, wall_segs, opening_cells, eps):
    """joined-at (shared endpoints), placed-in (containment), room adjacency
    through passable doors — the routing substrate."""
    segs = list(wall_segs.items())
    for i, (a, (sa, ea)) in enumerate(segs):
        for b, (sb, eb) in segs[i + 1:]:
            if min(dist(sa, sb), dist(sa, eb), dist(ea, sb), dist(ea, eb)) <= eps:
                g.edge("joined-at", a, b)

    # rooms with polygons or centers; furniture/floor containment
    rooms = [(nd, nd["attrs"].get("label")) for nd in g.nodes if nd["kind"] == "room"]
    for nd in g.nodes:
        if nd["kind"] not in ("furniture", "floor"):
            continue
        p = nd["geometry"]["position"][:2]
        best = None
        for rn, _lbl in rooms:
            poly = rn["geometry"].get("points")
            if poly and len(poly) >= 3 and point_in_poly(p, poly):
                best = rn
                break
        if best is None:  # fall back to nearest room center
            rc = [(dist(p, rn["geometry"]["position"][:2]), rn) for rn, _ in rooms]
            best = min(rc, key=lambda t: t[0])[1] if rc else None
        if best is not None:
            g.edge("placed-in", nd["id"], best["id"],
                   weight=dist(p, best["geometry"]["position"][:2]))

    # room↔room adjacency via doors: passable portal edges
    for onode in (g._by_id[o] for o in opening_cells if o in g._by_id):
        op = onode["geometry"]["position"][:2]
        near_rooms = sorted((dist(op, rn["geometry"]["position"][:2]), rn["id"])
                            for rn, _ in rooms)
        if len(near_rooms) >= 2:
            (d1, r1), (d2, r2) = near_rooms[0], near_rooms[1]
            g.edge("passable", r1, onode["id"], weight=d1)
            g.edge("passable", r2, onode["id"], weight=d2)

# ---------------------------------------------------------------- telemetry
def finalize(g, input_kind, input_path):
    xs = [p for nd in g.nodes for p in [nd["geometry"]["position"][0]]]
    ys = [p for nd in g.nodes for p in [nd["geometry"]["position"][1]]]
    counts = defaultdict(int)
    for nd in g.nodes:
        counts[nd["kind"]] += 1
    ec = defaultdict(int)
    for e in g.edges:
        ec[e["kind"]] += 1
    return {
        "format": "quilt-cellgraph/1",
        "source": {
            "tool": "openplan3d-quilt-twin",
            "inputKind": input_kind,
            "input": input_path,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "opcodeMap": {
            "BIND": "instantiate object (draw wall / place furniture)",
            "LINK": "parent / constraint (door→wall hosting, room bounds, joins)",
            "EFFECT": "transform (move / rotate / resize / recolor)",
            "VIEW": "render / camera (plan2d, walk3d, svg/dxf/pdf/png export)",
            "TICK": "physics / sim step (walkthrough, version-history snapshots)",
            "FORGET": "delete (wall delete cascades along hosted-in edges)",
        },
        "nodes": g.nodes,
        "edges": g.edges,
        "views": [
            {"id": "view:plan2d", "kind": "render", "camera": "orthographic-top"},
            {"id": "view:walk3d", "kind": "camera", "camera": "first-person"},
            {"id": "view:export", "kind": "render", "camera": "projection",
             "formats": ["svg", "dxf", "pdf", "png"]},
        ],
        "ticks": [
            {"id": "tick:snapshot", "kind": "version-history"},
            {"id": "tick:sim", "kind": "physics"},
        ],
        "telemetry": {
            "counts": {"nodes": len(g.nodes), "edges": len(g.edges),
                       "byKind": dict(counts), "edgesByKind": dict(ec)},
            "bounds": ({"minX": round(min(xs), 1), "maxX": round(max(xs), 1),
                        "minY": round(min(ys), 1), "maxY": round(max(ys), 1)}
                       if xs and ys else None),
            "stories": sorted({nd["story"] for nd in g.nodes}),
            "units": "cm",
        },
    }

# ---------------------------------------------------------------- main
def detect(data):
    if "floors" in data and isinstance(data.get("floors"), list) and \
       data["floors"] and "walls" in data["floors"][0]:
        return "project"
    if "walls" in data and "sections" in data:
        return "roomplan"
    return None

def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("input", help="openPlan3D Project JSON or Apple RoomPlan JSON")
    ap.add_argument("-o", "--output", default="-", help="output cell-graph JSON path (- = stdout)")
    ap.add_argument("--format", choices=("project", "roomplan"), help="force input kind")
    args = ap.parse_args()

    with open(args.input) as f:
        data = json.load(f)
    kind = args.format or detect(data)
    if kind == "roomplan":
        g, ik = build_roomplan(data)
    elif kind == "project":
        g, ik = build_project(data)
    else:
        sys.exit("error: unrecognized input format (need .floors[].walls or .walls+.sections)")

    doc = finalize(g, ik, args.input)
    out = json.dumps(doc, indent=2)
    if args.output == "-":
        print(out)
    else:
        with open(args.output, "w") as f:
            f.write(out + "\n")
        t = doc["telemetry"]["counts"]
        print(f"twin: {t['nodes']} nodes / {t['edges']} edges "
              f"({t['byKind']}) -> {args.output}", file=sys.stderr)

if __name__ == "__main__":
    main()
