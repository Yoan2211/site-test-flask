import os
import time
import uuid
from urllib.parse import urljoin
from flask import current_app
from werkzeug.utils import secure_filename

# -----------------------
def make_public_asset_url(rel_path: str) -> str | None:
    """Construit l’URL publique d’un fichier statique."""
    base_url = current_app.config.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base_url or not rel_path:
        return None
    return urljoin(f"{base_url}/", f"static/{rel_path}")

# -----------------------
def parse_time_to_seconds(time_str: str) -> int:
    """Convertit hh:mm:ss (ou mm:ss) en secondes."""
    if not time_str:
        return 0
    parts = [p for p in time_str.strip().split(":") if p != ""]
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0

    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h = 0
        m, s = parts
    elif len(parts) == 1:
        h = 0
        m = parts[0]
        s = 0
    else:
        return 0

    return h * 3600 + m * 60 + s

# -----------------------
def format_pace_mmss(pace_seconds: float) -> str:
    """Retourne 'm:ss' pour une allure en secondes/km."""
    if pace_seconds <= 0:
        return ""
    total = int(round(pace_seconds))
    m = total // 60
    s = total % 60
    return f"{m}:{s:02d}"

# -----------------------
def save_route_file(f) -> str | None:
    """Sauvegarde un fichier de tracé dans UPLOAD_FOLDER."""
    if not f or f.filename.strip() == "":
        return None

    name = secure_filename(f.filename)
    ext = os.path.splitext(name)[1].lower()

    allowed_exts = current_app.config.get("ALLOWED_ROUTE_EXT", {".gpx", ".tcx", ".txt"})
    if ext not in allowed_exts:
        return None

    timestamp = int(time.time())
    new_name = f"{uuid.uuid4().hex}_{timestamp}{ext}"
    upload_folder = current_app.config.get("UPLOAD_FOLDER", "static/uploads")
    os.makedirs(upload_folder, exist_ok=True)

    path = os.path.join(upload_folder, new_name)
    f.save(path)

    return f"uploads/{new_name}"
