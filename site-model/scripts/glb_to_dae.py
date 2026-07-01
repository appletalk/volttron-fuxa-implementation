#!/usr/bin/env python
"""Convert a Blender Z-up glTF/GLB to a Google-Earth-friendly COLLADA .dae:
   Z_UP, meter units, per-object phong diffuse colour, double-sided.
   Usage: glb_to_dae.py in.glb out.dae
"""
import sys
import numpy as np
import trimesh
import collada


def get_color(mesh):
    """Best-effort single representative RGB (0..1) for a trimesh geometry.

    NOTE: trimesh stores baseColorFactor / main_color as uint8 (0..255), so
    everything is normalised to the 0..1 floats COLLADA (and Google Earth)
    require -- passing 0..255 straight through makes Google Earth clamp to
    white.
    """
    v = getattr(mesh, "visual", None)
    mat = getattr(v, "material", None)
    rgb = None
    if mat is not None:
        bc = getattr(mat, "baseColorFactor", None)
        if bc is not None:
            rgb = [float(c) for c in bc[:3]]
        else:
            mc = getattr(mat, "main_color", None)
            if mc is not None:
                rgb = [float(c) for c in mc[:3]]
    if rgb is None:
        try:
            rgb = [float(c) for c in v.main_color[:3]]
        except Exception:
            rgb = [0.6, 0.6, 0.6]
    if max(rgb) > 1.0:                     # 0..255 -> 0..1
        rgb = [c / 255.0 for c in rgb]
    return tuple(rgb)


def main(glb, dae):
    scene = trimesh.load(glb, process=False)
    if isinstance(scene, trimesh.Trimesh):
        geoms = [(scene, np.eye(4), "mesh")]
    else:
        geoms = []
        for node in scene.graph.nodes_geometry:
            T, gname = scene.graph[node]
            geoms.append((scene.geometry[gname].copy(), np.asarray(T), node))

    dae_doc = collada.Collada()
    colcache = {}

    def material_for(rgb):
        key = tuple(round(c, 4) for c in rgb)
        if key in colcache:
            return colcache[key]
        idx = len(colcache)
        eff = collada.material.Effect(
            "eff_%d" % idx, [], "phong",
            diffuse=tuple(rgb), specular=(0.05, 0.05, 0.05), shininess=10.0)
        eff.double_sided = True
        m = collada.material.Material("mat_%d" % idx, "mat_%d" % idx, eff)
        dae_doc.effects.append(eff)
        dae_doc.materials.append(m)
        colcache[key] = m
        return m

    nodes = []
    for i, (g, T, name) in enumerate(geoms):
        g.apply_transform(T)                       # bake node transform -> world coords
        rgb = get_color(g)
        verts = g.vertices.astype(np.float32).reshape(-1)
        faces = g.faces.astype(np.int32).reshape(-1)
        vsrc = collada.source.FloatSource("v%d" % i, verts, ("X", "Y", "Z"))
        geom = collada.geometry.Geometry(dae_doc, "geo%d" % i, name, [vsrc])
        inp = collada.source.InputList()
        inp.addInput(0, "VERTEX", "#v%d" % i)
        tset = geom.createTriangleSet(faces, inp, "mref")
        geom.primitives.append(tset)
        dae_doc.geometries.append(geom)
        matnode = collada.scene.MaterialNode("mref", material_for(rgb), inputs=[])
        gnode = collada.scene.GeometryNode(geom, [matnode])
        nodes.append(collada.scene.Node("n%d" % i, children=[gnode], name=name))

    myscene = collada.scene.Scene("scene", nodes)
    dae_doc.scenes.append(myscene)
    dae_doc.scene = myscene
    dae_doc.assetInfo.upaxis = collada.asset.UP_AXIS.Z_UP
    dae_doc.assetInfo.unitname = "meter"
    dae_doc.assetInfo.unitmeter = 1.0
    dae_doc.write(dae)
    print("wrote %s: %d geoms, %d materials" % (dae, len(geoms), len(colcache)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
