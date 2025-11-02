"""
services/stl_manager.py
=======================
Gestion de la génération de fichiers STL à partir de tracés Strava.
Contient les fonctions utilitaires de projection, nettoyage et extrusion 3D.
"""

import io
import math
import numpy as np
import trimesh
import polyline

# Import local pour l’intégration Strava
from services.strava_service import StravaService


# === Fonctions utilitaires internes ===

def _latlon_to_mercator(lat, lon):
    """Convertit des coordonnées latitude/longitude en mètres (projection Mercator)."""
    R = 6378137.0
    x = math.radians(lon) * R
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
    return x, y


def _dedupe_close_points(arr, eps=1e-9):
    """Supprime les points trop proches les uns des autres (pour éviter le bruit)."""
    if len(arr) < 2:
        return arr
    keep = [arr[0]]
    for p in arr[1:]:
        if np.linalg.norm(p - keep[-1]) > eps:
            keep.append(p)
    return np.array(keep, dtype=float)


def _safe_unit(v, fallback=None, eps=1e-12):
    """Normalise un vecteur, avec gestion de sécurité pour les très petites normes."""
    n = np.linalg.norm(v)
    if n < eps or not np.isfinite(n):
        return fallback if fallback is not None else np.array([1.0, 0.0], dtype=float)
    return v / n


# === Fonction principale ===

def generate_stl_from_activity(
    activity_id: str,
    thickness: float = 1.2,
    height: float = 0.6,
    max_x_mm: float = 28.0,
    max_y_mm: float = 50.0,
    token: str | None = None
) -> bytes:
    """
    Génère un STL 3D à partir du tracé GPS d'une activité Strava.

    Args:
        activity_id: ID de l’activité Strava.
        thickness: Largeur du trait extrudé (en mm).
        height: Hauteur d’extrusion (en mm).
        max_x_mm / max_y_mm: Dimensions maximales du cadre (en mm).
        token: Jeton Strava à utiliser, ou None pour utiliser la session active.

    Returns:
        bytes: Contenu du fichier STL prêt à sauvegarder.
    """
    # 1️⃣ Auth + récupération activité
    if token is None:
        user, token = StravaService.get_token_from_session()
        if not token:
            raise RuntimeError("Utilisateur non connecté à Strava.")

    activity = StravaService.fetch_activity(token, activity_id)
    poly = (activity or {}).get("map", {}).get("summary_polyline")
    if not poly:
        raise RuntimeError("Aucun tracé GPS trouvé pour cette activité.")

    coords = polyline.decode(poly)
    if not coords or len(coords) < 2:
        raise RuntimeError("Tracé GPS trop court ou invalide.")

    # 2️⃣ Projection Mercator
    merc = np.array([_latlon_to_mercator(lat, lon) for (lat, lon) in coords], dtype=float)
    merc = _dedupe_close_points(merc)

    # 3️⃣ Mise à l’échelle uniforme + centrage
    minx, miny = merc.min(axis=0)
    maxx, maxy = merc.max(axis=0)
    span_x, span_y = maxx - minx, maxy - miny
    if span_x <= 0 or span_y <= 0:
        raise RuntimeError("Tracé dégénéré.")

    scale = min(max_x_mm / span_x, max_y_mm / span_y)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2

    P = np.empty_like(merc, dtype=float)
    P[:, 0] = (merc[:, 0] - cx) * scale
    P[:, 1] = (merc[:, 1] - cy) * scale

    npts = len(P)
    if npts < 2:
        raise RuntimeError("Trop peu de points GPS pour un tracé continu.")

    # 4️⃣ Tangentes + normales
    half_w = float(thickness) / 2.0
    H = float(height)
    eps = 1e-12

    # Tangentes
    T = np.zeros_like(P)
    for i in range(npts):
        if i == 0:
            fwd = _safe_unit(P[i + 1] - P[i])
            T[i] = fwd
        elif i == npts - 1:
            back = _safe_unit(P[i] - P[i - 1])
            T[i] = back
        else:
            v_prev = _safe_unit(P[i] - P[i - 1])
            v_next = _safe_unit(P[i + 1] - P[i])
            s = _safe_unit(v_prev + v_next, fallback=v_next)
            T[i] = s

    # Normales
    N = np.zeros_like(P)
    for i in range(npts):
        n = np.array([-T[i, 1], T[i, 0]], dtype=float)
        N[i] = _safe_unit(n)

    # 5️⃣ Construction de la bande (L/R)
    L = P + N * half_w
    R = P - N * half_w

    # 6️⃣ Sommets 3D et faces
    verts = []
    for i in range(npts):
        Lb = np.array([L[i, 0], L[i, 1], 0.0])
        Rb = np.array([R[i, 0], R[i, 1], 0.0])
        Lt = np.array([L[i, 0], L[i, 1], H])
        Rt = np.array([R[i, 0], R[i, 1], H])
        verts.extend([Lb, Rb, Lt, Rt])
    verts = np.asarray(verts, dtype=float)

    faces = []

    def quad(a, b, c, d):
        faces.append([a, b, c])
        faces.append([a, c, d])

    for i in range(npts - 1):
        i0 = i * 4
        i1 = (i + 1) * 4
        Lb0, Rb0, Lt0, Rt0 = i0 + 0, i0 + 1, i0 + 2, i0 + 3
        Lb1, Rb1, Lt1, Rt1 = i1 + 0, i1 + 1, i1 + 2, i1 + 3

        quad(Lb0, Rb0, Rb1, Lb1)  # bas
        quad(Lt0, Lt1, Rt1, Rt0)  # haut
        quad(Lb0, Lb1, Lt1, Lt0)  # côté gauche
        quad(Rb0, Rt0, Rt1, Rb1)  # côté droit

    # Bouchons
    Lb0, Rb0, Lt0, Rt0 = 0, 1, 2, 3
    quad(Lb0, Rb0, Rt0, Lt0)
    j = (npts - 1) * 4
    LbN, RbN, LtN, RtN = j + 0, j + 1, j + 2, j + 3
    quad(LbN, LtN, RtN, RbN)

    # 7️⃣ Création du mesh
    mesh = trimesh.Trimesh(vertices=verts, faces=np.asarray(faces, dtype=np.int64), process=False)
    if not np.all(np.isfinite(mesh.vertices)):
        raise RuntimeError("Vertices non finis (NaN/inf) après génération.")

    zmin = mesh.bounds[0][2]
    if np.isfinite(zmin) and abs(zmin) > eps:
        mesh.apply_translation([0.0, 0.0, -zmin])

    # 8️⃣ Export STL
    buf = io.BytesIO()
    mesh.export(file_obj=buf, file_type='stl')
    buf.seek(0)
    return buf.read()



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