# text_to_stl_visible_scaled.py
import io
import os
import numpy as np
import trimesh
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties


# =============================
# 🔧 Fonctions utilitaires
# =============================
def _safe_unit(v, fallback=None):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        return np.asarray(fallback if fallback is not None else v, dtype=float)
    return v / n


def _dedupe_close_points(P, tol=0.05):
    if P is None or len(P) == 0:
        return P
    out = [P[0]]
    for i in range(1, len(P)):
        if np.linalg.norm(P[i] - out[-1]) >= tol:
            out.append(P[i])
    if len(out) >= 2 and np.allclose(out[0], out[-1]):
        out = out[:-1]
    return np.asarray(out, dtype=float)


# =============================
# ✏️ Génération du texte 2D
# =============================
def _text_to_polylines_2d(text, font_name="DejaVu Sans", size_mm=12.0):
    if not text.strip():
        raise RuntimeError("Texte vide")

    size_pt = size_mm * 72.0 / 25.4  # mm → points
    fp = FontProperties(family=font_name, size=size_pt)
    tp = TextPath((0, 0), text, prop=fp, usetex=False)

    polys = tp.to_polygons()
    if not polys:
        raise RuntimeError("Aucun contour généré")

    polylines = []
    for poly in polys:
        arr = np.asarray(poly, dtype=float)
        if arr.shape[0] < 2:
            continue

        # 🔁 Conversion points → mm
        arr[:, 0] *= 25.4 / 72.0
        arr[:, 1] *= 25.4 / 72.0
        arr = _dedupe_close_points(arr)
        if len(arr) >= 3:
            polylines.append(arr)
    return polylines


# =============================
# 🧱 Conversion en maillage 3D
# =============================
def _polyline_to_ribbon_mesh(P, thickness=1.6, height=1.2):
    npts = len(P)
    if npts < 3:
        raise RuntimeError("Pas assez de points")

    T = np.zeros_like(P)
    for i in range(npts):
        im1, ip1 = (i - 1) % npts, (i + 1) % npts
        v_prev = _safe_unit(P[i] - P[im1])
        v_next = _safe_unit(P[ip1] - P[i])
        T[i] = _safe_unit(v_prev + v_next, fallback=v_next)

    N = np.zeros_like(P)
    for i in range(npts):
        n = np.array([-T[i, 1], T[i, 0]])
        N[i] = _safe_unit(n)

    half_w = thickness / 2
    H = height
    L = P + N * half_w
    R = P - N * half_w

    verts = []
    for i in range(npts):
        Lb, Rb = [L[i, 0], L[i, 1], 0], [R[i, 0], R[i, 1], 0]
        Lt, Rt = [L[i, 0], L[i, 1], H], [R[i, 0], R[i, 1], H]
        verts.extend([Lb, Rb, Lt, Rt])
    verts = np.asarray(verts)

    faces = []
    def quad(a, b, c, d):
        faces.append([a, b, c])
        faces.append([a, c, d])

    for i in range(npts):
        i0, i1 = i * 4, ((i + 1) % npts) * 4
        Lb0, Rb0, Lt0, Rt0 = i0, i0 + 1, i0 + 2, i0 + 3
        Lb1, Rb1, Lt1, Rt1 = i1, i1 + 1, i1 + 2, i1 + 3
        quad(Lb0, Rb0, Rb1, Lb1)
        quad(Lt0, Lt1, Rt1, Rt0)
        quad(Lb0, Lb1, Lt1, Lt0)
        quad(Rb0, Rt0, Rt1, Rb1)

    mesh = trimesh.Trimesh(vertices=verts, faces=np.asarray(faces))
    mesh.remove_unreferenced_vertices()
    return mesh


# =============================
# 🚀 Fonction principale
# =============================
def generate_txt2stl(
    text_or_path,
    font_name="DejaVu Sans",
    size_mm=14.0,
    max_x_mm=28.0,
    max_y_mm=83.0
):
    # 🧾 Lecture du texte
    if os.path.isfile(text_or_path):
        with open(text_or_path, "r", encoding="utf-8") as f:
            text = f.read().strip()
    else:
        text = text_or_path.strip()
    if not text:
        raise RuntimeError("Aucun texte fourni")

    # ✂️ Découper les lignes
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError("Aucune ligne valide détectée")

    meshes_all = []
    line_spacing = size_mm * 1.2  # espace vertical
    total_height_text = line_spacing * len(lines)

    # Génération du texte (avant scaling)
    for idx, line in enumerate(lines):
        polylines = _text_to_polylines_2d(line, font_name=font_name, size_mm=size_mm)
        line_meshes = [_polyline_to_ribbon_mesh(P, 1.6, 1.2) for P in polylines if len(P) >= 3]
        text_mesh = trimesh.util.concatenate(line_meshes)
        y_offset = -(idx * line_spacing)
        text_mesh.apply_translation([0, y_offset, 0])
        meshes_all.append(text_mesh)

    text_mesh = trimesh.util.concatenate(meshes_all)

    # === ⚖️ Mise à l’échelle automatique ===
    bounds = text_mesh.bounds
    width = bounds[1][0] - bounds[0][0]
    height = bounds[1][1] - bounds[0][1]

    scale_x = max_x_mm / width if width > 0 else 1
    scale_y = max_y_mm / height if height > 0 else 1
    scale = min(scale_x, scale_y)  # garder les proportions
    text_mesh.apply_scale(scale)

    # ➕ Base plate sous le texte
    minx, miny, minz = text_mesh.bounds[0]
    maxx, maxy, maxz = text_mesh.bounds[1]
    base = trimesh.creation.box(extents=[maxx - minx + 2, maxy - miny + 2, 1.0])
    base.apply_translation([(minx + maxx) / 2, (miny + maxy) / 2, -0.5])

    final = trimesh.util.concatenate([base, text_mesh])
    final.apply_translation([-final.bounds[0][0], -final.bounds[0][1], 0.0])  # centrer
    final.remove_unreferenced_vertices()
    final.fix_normals()

    buf = io.BytesIO()
    final.export(file_obj=buf, file_type="stl")
    buf.seek(0)
    return buf.read()


# =============================
# 🧪 Test local
# =============================
if __name__ == "__main__":
    os.makedirs("Test_Txt_stl", exist_ok=True)
    out = "Test_Txt_stl/output_text_scaled.stl"
    try:
        sample_text = "FINISHER 2025\n10 KM STRAVA"
        data = generate_txt2stl(sample_text, max_x_mm=28, max_y_mm=83)
        with open(out, "wb") as f:
            f.write(data)
        print(f"✅ Fichier '{out}' généré avec succès (mise à l’échelle auto)")
    except Exception as e:
        print("❌ Erreur :", e)
