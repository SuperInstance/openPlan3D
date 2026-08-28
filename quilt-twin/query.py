#!/usr/bin/env python3
"""
query.py — the routing lens over an openPlan3D quilt cell graph.

  near  : cells within a graph/euclidean radius of a source cell
  route : shortest portal route (room → door → room) between two cells

Cell selectors:
  id:<cell-id>         exact node id
  label:<text>         first node whose attrs.label matches (case-insensitive)
  kind:<kind>[:n]      nth node of a kind, e.g. kind:door:0

Stdlib only.
"""
import argparse
import heapq
import json
import math
import sys

def load(path):
    with open(path) as f:
        return json.load(f)

def select(doc, sel):
    nodes = {n["id"]: n for n in doc["nodes"]}
    if sel.startswith("id:"):
        return nodes.get(sel[3:])
    if sel.startswith("label:"):
        want = sel[6:].lower()
        for n in doc["nodes"]:
            if n["attrs"].get("label", "").lower() == want:
                return n
        return None
    if sel.startswith("kind:"):
        parts = sel.split(":")
        want, idx = parts[1], (int(parts[2]) if len(parts) > 2 else 0)
        same = [n for n in doc["nodes"] if n["kind"] == want]
        return same[idx] if 0 <= idx < len(same) else None
    return nodes.get(sel)

def adjacency(doc):
    adj = {n["id"]: [] for n in doc["nodes"]}
    for e in doc["edges"]:
        w = float(e.get("weight", 1.0))
        adj.setdefault(e["src"], []).append((e["dst"], w))
        adj.setdefault(e["dst"], []).append((e["src"], w))
    return adj

def dijkstra(adj, source):
    dist = {source: 0.0}
    parent = {}
    pq = [(0.0, source)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, math.inf):
            continue
        for v, w in adj.get(u, ()):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                parent[v] = u
                heapq.heappush(pq, (nd, v))
    return dist, parent

def path(parent, target):
    out, cur = [], target
    while cur is not None:
        out.append(cur)
        cur = parent.get(cur)
    return out[::-1]

def euclid(a, b):
    ax, ay = a["geometry"]["position"][:2]
    bx, by = b["geometry"]["position"][:2]
    return math.hypot(ax - bx, ay - by)

def kind_of(doc, nid):
    for n in doc["nodes"]:
        if n["id"] == nid:
            return n
    return None

def cmd_near(doc, src_sel, radius):
    src = select(doc, src_sel) or sys.exit(f"error: no cell matches {src_sel!r}")
    adj = adjacency(doc)
    dist, _ = dijkstra(adj, src["id"])
    hits = []
    for nid, d in sorted(dist.items(), key=lambda kv: kv[1]):
        if 0 < d <= radius:
            n = kind_of(doc, nid)
            hits.append({"id": nid, "kind": n["kind"],
                         "label": n["attrs"].get("label", ""),
                         "graphDist": round(d, 1),
                         "euclidDist": round(euclid(src, n), 1)})
    return {"query": "near", "from": {"id": src["id"], "label": src["attrs"].get("label", "")},
            "radius": radius, "units": "cm", "hits": hits}

def cmd_route(doc, a_sel, b_sel):
    a = select(doc, a_sel) or sys.exit(f"error: no cell matches {a_sel!r}")
    b = select(doc, b_sel) or sys.exit(f"error: no cell matches {b_sel!r}")
    # routing graph = only passable edges (rooms <-> doors)
    adj = {n["id"]: [] for n in doc["nodes"]}
    for e in doc["edges"]:
        if e["kind"] == "passable":
            w = float(e.get("weight", 1.0))
            adj.setdefault(e["src"], []).append((e["dst"], w))
            adj.setdefault(e["dst"], []).append((e["src"], w))
    dist, parent = dijkstra(adj, a["id"])
    if b["id"] not in dist:
        return {"query": "route", "from": a["id"], "to": b["id"], "reachable": False,
                "note": "no passable portal path (walls without doors block movement)"}
    seq = path(parent, b["id"])
    return {"query": "route", "from": {"id": a["id"], "label": a["attrs"].get("label", "")},
            "to": {"id": b["id"], "label": b["attrs"].get("label", "")},
            "reachable": True, "portalDist": round(dist[b["id"]], 1), "units": "cm",
            "path": [{"id": nid, "kind": kind_of(doc, nid)["kind"],
                      "label": kind_of(doc, nid)["attrs"].get("label", "")} for nid in seq]}

def main():
    ap = argparse.ArgumentParser(description="routing lens over a quilt cell graph")
    ap.add_argument("graph", help="cell-graph JSON (from twin.py)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("near", help="cells within radius of a source (graph distance)")
    n.add_argument("--from", dest="src", required=True)
    n.add_argument("--radius", type=float, default=400.0, help="cm (graph distance)")
    r = sub.add_parser("route", help="shortest portal route between two cells")
    r.add_argument("--from", dest="src", required=True)
    r.add_argument("--to", dest="dst", required=True)
    args = ap.parse_args()

    doc = load(args.graph)
    out = cmd_near(doc, args.src, args.radius) if args.cmd == "near" else cmd_route(doc, args.src, args.dst)
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
