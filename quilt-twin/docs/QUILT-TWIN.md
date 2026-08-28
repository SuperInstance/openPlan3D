# openPlan3D ⇄ Quilt Substrate — The Quilt-Twin

**Status:** working twin · 2026-08-27 · SuperInstance / OPENPLAN3D-TWIN lane

openPlan3D is a 2D/3D floor-plan editor (SvelteKit + Three.js). This twin
projects a floor plan onto the **quilt substrate**: the plan becomes a
**cell graph**, every edit becomes an opcode, every camera becomes a VIEW,
and every spatial question becomes a **routing problem**.

The claim being tested: *a floor plan is not geometry first — it is a graph
of spatial commitments. Geometry is just one attribute of the cells.*

---

## 1. The Core Mapping

| openPlan3D concept | Quilt substrate | Notes |
|---|---|---|
| **Project** | the cell-graph document | `format: quilt-cellgraph/1` |
| **Wall / Door / Window / Furniture / Stair / Column** | **CELL** (node) | one node per placed object, id = `cell:<kind>:<uuid>` |
| **Door/Window hosted in a wall** (`wallId`, RoomPlan `parentIdentifier`) | **LINK** edge `hosted-in` | parenting/constraint — the door cannot exist without the wall |
| **Room bounded by walls** (`room.walls: id[]`) | **LINK** edge `bounds` | room cell ↔ wall cells |
| **Furniture inside a room** (point-in-polygon) | **LINK** edge `placed-in` | derived by containment, not stored |
| **Wall ↔ wall endpoint join** (shared endpoints, ε≈10cm) | **LINK** edge `joined-at` | the structural skeleton — this is the routing substrate |
| **Element groups** (`ElementGroup.elementIds`) | **LINK** edge `grouped` | selection lifetime |
| **2D editor view** (ortho top-down) | **VIEW** `view:plan2d` | render/camera opcode target |
| **3D preview / walkthrough** (first-person camera) | **VIEW** `view:walk3d` | camera state = VIEW payload |
| **SVG/DXF/PDF/PNG export** | **VIEW** (materialized projection) | a render is a VIEW that got frozen to bytes |
| **JSON export/import** | cell-graph serialization | the twin format round-trips what matters |
| **Version history snapshots** | **TICK** cadence | each auto-save = one tick of the document |
| **Walkthrough physics / future sim** | **TICK** `tick:sim` | physics/sim step over cells |
| **Move / rotate / resize / scale** | **EFFECT** | transform application to a cell |
| **Delete object** (wall delete cascades to its doors/windows) | **FORGET** | cascade = following `hosted-in` edges backward |
| **Place object from catalog / draw wall** | **BIND** | instantiation of an entity into the graph |

## 2. The 5+1 Opcodes as 3D Planning Operations

- **BIND = instantiate.** Dragging a sofa from the 140-item catalog, or
  click-placing a wall: BIND creates the cell, seeds its transform, and
  immediately LINKs it (to a room by containment, to walls by joins).
- **LINK = parent/constraint.** The strongest structural fact in a floor
  plan is *hosting*: a door is hosted-in a wall (position is even stored as
  a 0–1 parameter *along* the wall, not absolute coordinates — a pure
  constraint expression). Apple RoomPlan says it outright with
  `parentIdentifier`. LINK is where the plan's skeleton lives.
- **EFFECT = transform.** `{position, rotation, scale}` diffs. Furniture
  rotation, wall endpoint drag, room recolor. Effects are local — they hit
  one cell — but *derived* edges (placed-in, joined-at) are recomputed,
  because effects have graph-visible consequences.
- **VIEW = render/camera.** 2D ortho plan, 3D perspective, walkthrough
  first-person. Every export format (SVG/DXF/PDF/PNG) is the same VIEW
  opcode with a different projection lens.
- **TICK = physics/sim step.** Walkthrough collision today; structural/
  lighting/sun simulation tomorrow. The tick advances all cells one step;
  the version-history snapshot loop is the document-level tick.
- **FORGET = delete.** Deleting a wall FORGETS the wall cell *and* cascades
  along `hosted-in` edges to its doors and windows — the cascade is a graph
  traversal, proof that deletion semantics in a plan are already relational.

## 3. The Routing Lens — space as a traversable graph

**"What is within X of Y?"** is answered two ways in this twin:

1. **Euclidean near-query** — Dijkstra over the cell graph with edge
   weights = straight-line distance. Cheap, geometry-blind.
2. **Portal routing** — the interesting one. Rooms and doors form a
   navigation graph: a room cell connects to another room cell *only
   through a door cell that is hosted-in a wall bounding both*. Distance =
   room-center → door → room-center. "Nearest reachable bathroom" ≠
   "nearest bathroom" — walls block, doors pass. **Space itself routes.**

This is the fleet routing doctrine applied to architecture: the cell graph
is a substrate any runtime can route over, the same way agents route over
the fleet graph. `query.py` implements `near` (within-radius by graph
 distance) and `route` (portal shortest-path A→B with the door sequence).

### 3.1 Verified results — the real scan

All numbers below are from the repo's `test-roomplan.json` (a real Apple
RoomPlan capture), twin rebuilt fresh 2026-08-27:

* **Twin size:** 45 nodes / 55 edges — rooms 4, walls 20, doors 4,
  windows 4, furniture 13; edges: `hosted-in` 8, `joined-at` 26,
  `placed-in` 13, `passable` 8. Full semantics, no lossy drops.
* **`route bedroom → bathroom`:** reachable, portal distance **694.9 cm**,
  path `bedroom → door → unidentified(room/hall) → door → bathroom` — the
  scan's "unidentified" section is revealed as the hallway that mediates
  the whole apartment. Two doors crossed; no wall is walked through.
* **`route bedroom₂ → bedroom₁`:** reachable, 748.7 cm, both bedrooms
  connect only via the hallway (door → hall → door), never directly —
  exactly how the rooms physically relate.
* **`near bedroom --radius 400`:** 28 hits. The lens splits: wall
  `cell:wall:ab1bb866a7` sits at graph distance **360.3 cm** but euclidean
  **180.7 cm** — the wall is *near* but only *reachable* by routing out the
  door and back along a hosted-in edge. Graph distance encodes traversal,
  not line-of-sight. Inverse case also present: wall at euclid 561 cm but
  graph 364 cm (reachable through the joined wall skeleton).
* **Negative test:** `route furniture → bathroom` → `reachable: false`.
  Portal routing only spans the navigation substrate (rooms + doors); a
  sofa is not a place you can be. The unreachable branch is load-bearing,
  not dead code.

Captured outputs live in `examples/routing.json`.

The converter's **native Project path** (openPlan3D's own JSON export) is
also verified: on a two-room synthetic project it emits correct room
centroids, `bounds` edges, and a portal route through the connecting door
(`room → door → room`, 350 cm). This re-fire fixed two latent bugs in that
path — walls were being looked up in the wrong key-space (project wall ids
vs cell ids, which zeroed every room polygon and silently dropped all
`bounds` edges) and a distance-tie in the nearest-room fallback crashed on
dict comparison. The RoomPlan path was unaffected and reproduces the 45/55
scan identically.

## 4. Artifacts

| File | What it is |
|---|---|
| `../twin.py` | converter — openPlan3D native Project JSON **or** Apple RoomPlan JSON → cell graph (Python ≥3.9, stdlib only) |
| `../query.py` | routing CLI — `near` / `route` over the emitted cell graph |
| `../examples/roomplan.cellgraph.json` | real artifact generated from the repo's `test-roomplan.json` (45 nodes / 55 edges) |
| `../examples/routing.json` | captured routing-lens results over the real scan |

```bash
# build the twin from a real plan (RoomPlan or native export)
python3 quilt-twin/twin.py test-roomplan.json -o quilt-twin/examples/roomplan.cellgraph.json
# -> twin: 45 nodes / 55 edges ({'room': 4, 'wall': 20, 'door': 4, 'window': 4, 'furniture': 13})

# what's within 4 m (graph distance) of the bedroom?
python3 quilt-twin/query.py quilt-twin/examples/roomplan.cellgraph.json near --from label:bedroom --radius 400

# shortest portal route bedroom -> bathroom (two doors, via the hallway)
python3 quilt-twin/query.py quilt-twin/examples/roomplan.cellgraph.json route --from label:bedroom --to label:bathroom

# cell selectors: label:<text> | kind:<kind>[:n] | id:<cell-id>
python3 quilt-twin/query.py quilt-twin/examples/roomplan.cellgraph.json route --from kind:room:1 --to kind:room:0
```

## 5. Cell-graph document shape

```jsonc
{
  "format": "quilt-cellgraph/1",
  "source": { "tool": "openplan3d-quilt-twin", "inputKind": "roomplan|project", ... },
  "opcodeMap": { "BIND": "instantiate", "LINK": "parent/constraint", ... },
  "nodes": [ { "id": "cell:wall:...", "kind": "wall", "story": 0,
               "geometry": { "position": [x,y,z], "points": [[x,y],...] },
               "attrs": { "label": "bedroom", ... },
               "telemetry": { "length": 380.0, "height": 270.0 } } ],
  "edges": [ { "id": "e:...", "kind": "hosted-in|bounds|joined-at|placed-in|grouped|passable",
               "src": "...", "dst": "...", "weight": 92.4 } ],
  "views":  [ { "id": "view:plan2d", "kind": "render", "camera": "orthographic-top" },
              { "id": "view:walk3d", "kind": "camera", "camera": "first-person" } ],
  "ticks":  [ { "id": "tick:snapshot", "kind": "version-history" },
              { "id": "tick:sim", "kind": "physics" } ],
  "telemetry": { "counts": {...}, "bounds": {...}, "stories": 1 }
}
```

Units: centimeters in the X-Y plane (openPlan3D native convention), height
in cm, stories indexed from 0. Positions are `[x, y, z]` with z = height.

## 6. Engine-porting note

Godot/Unity/Unreal ports are a separate lane. The point of this twin: any
engine, runtime, or agent that can read JSON and traverse a graph can
consume the plan **before** anyone writes a single line of engine code.
The cell graph is the port target — the port is a VIEW with extra steps.
