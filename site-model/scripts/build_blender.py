"""Build Sunfield Solar plant.glb from build/plan.json, inside Blender.

Reads the parametric plan (boxes + cylinders, per-material), builds one joined
mesh per material (`grp_<mat>`) via from_pydata, assigns the plan's 0..1 colours,
and exports a **Z-up** glTF/GLB (Blender 5 dropped native COLLADA -> glb_to_dae.py
converts). Run either via BlenderMCP `execute_blender_code`, or headless:

    blender --background --python scripts/build_blender.py

Output: build/plant.glb  (then glb_to_dae.py -> plant.dae -> make_kmz.py -> KMZ).
"""
import bpy, json, math, os
from mathutils import Euler, Vector

BUILD = "/home/keith/development/volttron-fuxa-implementation/site-model/build"


def build():
    plan = json.load(open(os.path.join(BUILD, "plan.json")))
    prims, mats = plan["prims"], plan["mats"]

    # wipe scene + orphan data for a clean rebuild
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for coll in (bpy.data.meshes, bpy.data.materials):
        for d in list(coll):
            coll.remove(d)

    acc = {}   # mat -> ([verts], [faces])

    def box(mat, dims, pos, rot):
        hx, hy, hz = dims[0] / 2, dims[1] / 2, dims[2] / 2
        corners = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
                   (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
        E, P = Euler(tuple(rot), "XYZ"), Vector(pos)
        v, f = acc.setdefault(mat, ([], []))
        b = len(v)
        for c in corners:
            vc = Vector(c); vc.rotate(E); vc += P
            v.append((vc.x, vc.y, vc.z))
        for q in [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1),
                  (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]:
            f.append(tuple(b + i for i in q))

    def cyl(mat, r, h, pos, rot, seg=10):
        E, P = Euler(tuple(rot), "XYZ"), Vector(pos)
        v, f = acc.setdefault(mat, ([], []))
        b = len(v)
        ring = [(r * math.cos(2 * math.pi * i / seg), r * math.sin(2 * math.pi * i / seg))
                for i in range(seg)]
        for z in (-h / 2, h / 2):
            for x, y in ring:
                vc = Vector((x, y, z)); vc.rotate(E); vc += P
                v.append((vc.x, vc.y, vc.z))
        for i in range(seg):
            j = (i + 1) % seg
            f.append((b + i, b + j, b + seg + j, b + seg + i))
        f.append(tuple(b + i for i in range(seg)))              # bottom cap
        f.append(tuple(b + seg + i for i in range(seg - 1, -1, -1)))   # top cap

    for p in prims:
        if p["kind"] == "box":
            if p["dims"][0] == 0:
                continue
            box(p["mat"], p["dims"], p["pos"], p["rot"])
        else:
            cyl(p["mat"], p["r"], p["h"], p["pos"], p["rot"])

    for mat, (v, f) in acc.items():
        me = bpy.data.meshes.new("m_" + mat)
        me.from_pydata(v, [], f)
        me.update()
        ob = bpy.data.objects.new("grp_" + mat, me)
        bpy.context.collection.objects.link(ob)
        col = mats[mat]
        m = bpy.data.materials.new("mat_" + mat)
        m.use_nodes = True
        m.diffuse_color = (col[0], col[1], col[2], 1.0)
        bsdf = m.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (col[0], col[1], col[2], 1.0)
            if "Roughness" in bsdf.inputs:
                bsdf.inputs["Roughness"].default_value = 0.85
        me.materials.append(m)

    out = os.path.join(BUILD, "plant.glb")
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB",
                              export_yup=False, use_selection=False)
    nv = sum(len(v) for v, _ in acc.values())
    print("wrote %s | %d material-groups | %d verts | %d prims"
          % (out, len(acc), nv, len(prims)))


build()
