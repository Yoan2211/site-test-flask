# app.py
# -*- coding: utf-8 -*-
"""
@author: YA
To DO :
1) Windows Powershell: ouvrir un terminal dans le projet du site> ngrok config add-authtoken 32hpV03K5HSmz6ObDkYGhtsT78E_7Gx526uNHLqXeRwTCt6a2
2) CMD: ngrok http http://localhost:5000
3) Take URL from "Forwarding" (corresponding to NGROK_SERVER_URL in config.py)
4) Change NGROK_SERVER_URL in config.py
4bis) IF Strava user change : go to https://www.strava.com/settings/api to change STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET
5) Change Webhook in Mollie
6) Change Webhook in Strava
"""
from flask import render_template, request, redirect, url_for, session, flash
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView

import os
import uuid
import json
import time
from datetime import timedelta
from datetime import datetime
import requests



# Logique métier (API, traitement, etc.)
from services.manage_sendgrid import envoyer_email_sendgrid_Client, envoyer_email_sendgrid_Admin
from services.googledrive import upload_to_google_drive_cmdFile
from services.strava_service import StravaService

import polyline

# Mollie SDK
from mollie.api.client import Client
from routes.mollie import get_cart_items, cart_total
from models.db_database import db, User, BillingInfo, Order, OrderPhoto



# Utils
from utils.tts_utils import format_pace_mmss, parse_time_to_seconds, make_public_asset_url, save_route_file
from utils.image_utils import render_track_image

import shutil

from __init__ import create_app
# ------------------------------------------------- Config -------------------------------------------------
# ----------------- Chargement app Flask & Routes -----------------
app = create_app()

# 🕒 Durée de vie des sessions Flask (ex: 30 minutes)
SESSION_TIMEOUT_MINUTES = app.config["SESSION_TIMEOUT_MINUTES"]
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=SESSION_TIMEOUT_MINUTES)

with app.app_context():
    db.create_all()
    StravaService.cleanup_expired_connections()



# Clés et URLs
MOLLIE_SECRET_KEY = app.config["MOLLIE_SECRET_KEY"]
MOLLIE_API_KEY = app.config["MOLLIE_API_KEY"]
SENDGRID_API_KEY = app.config["SENDGRID_API_KEY"]
PUBLIC_BASE_URL = app.config["PUBLIC_BASE_URL"]
WEBHOOK_URL = app.config["WEBHOOK_URL"]

# Uploads
UPLOAD_FOLDER = app.config["UPLOAD_FOLDER"]
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
MAX_CONTENT_LENGTH = app.config["MAX_CONTENT_LENGTH"]
ALLOWED_ROUTE_EXT = app.config["ALLOWED_ROUTE_EXT"]


# Prix
PRICES = app.config["PRICES"]

SESSION_TIMEOUT_MINUTES = app.config["SESSION_TIMEOUT_MINUTES"] 

# Mollie
# Clé attendue: test_... ou live_... ; ne pas préfixer "Bearer "
if not (MOLLIE_API_KEY.startswith("test_") or MOLLIE_API_KEY.startswith("live_")):
    raise RuntimeError(
        "MOLLIE_API_KEY manquante/invalide. Utiliser une clé Mollie commençant par test_ ou live_, sans 'Bearer '."
    )

mollie_client = Client()
mollie_client.set_api_key(MOLLIE_API_KEY)
# -------------------------------------------------------------------------------------------------------------
# ==========================================================
# 🧩 ROUTE TEST STL VIEWER
# ==========================================================
@app.route("/viewer")
def stl_viewer():
    """
    Affiche un viewer Three.js pour un fichier STL (interne ou uploadé)
    """
    # Exemple de fichier statique
    stl_url = url_for("static", filename="stl/track_sample.stl")
    return render_template("viewer.html", stl_url=stl_url)

# ----------------- Routes -----------------
@app.route("/")
def home():
    return render_template("home.html")

# -------------------
# Gobelet
# -------------------
@app.route("/gobelet", methods=["GET", "POST"])
def gobelet():

    selected_activity = None
    user = None
    strava_token = None
    strava_connected = False

    print("\n🟢 [DEBUG] --- Accès à /gobelet ---")

    # --- Cas utilisateur connecté ---
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user:
            strava_token = StravaService.get_token(user)
            strava_connected = bool(strava_token)
            user_has_strava_linked = bool(user.strava_access_token)
            selected_activity = session.get("selected_activity") if strava_token else None
            print(f"🟢 [DEBUG] Utilisateur connecté : {user.email if user else 'inconnu'}")
        else:
            session.pop("selected_activity", None)
            user_has_strava_linked = False
    else:
        # --- Cas invité (guest Strava) ---
        token = session.get("strava_token")
        expires_at = session.get("strava_expires_at", 0)
        if token and time.time() < expires_at:
            strava_token = token
            strava_connected = True
            user_has_strava_linked = False
            selected_activity = session.get("selected_activity")
            print("🟢 [DEBUG] Session Strava invitée active.")
        else:
            session.pop("selected_activity", None)
            session.pop("strava_token", None)
            session.pop("strava_expires_at", None)
            user_has_strava_linked = False
            print("🔴 [DEBUG] Aucun token Strava valide en session.")

    # --- LOG ACTIVITÉ ---
    if selected_activity:
        print(f"🟡 [DEBUG] Activité sélectionnée : {selected_activity.get('id')} / {selected_activity.get('name', 'N/A')}")
    else:
        print("🔴 [DEBUG] Aucune activité sélectionnée actuellement.")

    # --- GESTION DU FORMULAIRE ---
    if request.method == "POST":
        print("🧾 [DEBUG] FORM DATA:", request.form)
        color = request.form.get("color", "blanc")
        try:
            qty = max(1, int(request.form.get("qty", "1")))
        except ValueError:
            qty = 1

        add_results = request.form.get("add_results") == "on"

        results_data = None
        if add_results:
            if selected_activity:
                time_str = selected_activity["time"]
                distance = selected_activity["distance"]
                pace = selected_activity["pace"]
            else:
                time_str = request.form.get("time", "").strip()
                try:
                    distance = float(request.form.get("distance", 0))
                except ValueError:
                    distance = 0.0
                pace = None
                total_seconds = parse_time_to_seconds(time_str)
                if total_seconds > 0 and distance > 0:
                    pace = format_pace_mmss(total_seconds / distance)

            results_data = {"time": time_str, "distance": distance, "pace": pace}

        add_route = request.form.get("add_route") == "on"
        route_url = None
        route_local = None

        if add_route:
            if selected_activity and selected_activity.get("polyline"):
                route_url = url_for("strava.track", activity_id=selected_activity["id"])
                route_local = None
            else:
                route_file = request.files.get("route_file")
                if route_file:
                    route_rel = save_route_file(route_file)
                    route_url = make_public_asset_url(route_rel)
                    route_local = os.path.join("static", route_rel)

        # Calcul prix
        unit = PRICES["BASE"]
        if add_results:
            unit += PRICES["RESULTS"]
        if add_route:
            unit += PRICES["ROUTE"]
        total = round(unit * qty, 2)
        
        activity_id_opt = selected_activity["id"] if (selected_activity and selected_activity.get("id")) else None

        # Ajout panier
        item = {
            "id": uuid.uuid4().hex,
            "sku": "gobelet",
            "name": "Gobelet personnalisé",
            "image": "img/gobelet.jpg",
            "color": color,
            "qty": qty,
            "unit_price": round(unit, 2),
            "total": total,
            "options": {
                "add_results": add_results,
                "results": results_data,
                "add_route": add_route,
                "route_url": route_url,
                "route_local": route_local,
                "from_strava": bool(selected_activity),
                "activity_id": activity_id_opt
            },
        }

        add_cart_item(item)
        session.pop("selected_activity", None)
        return redirect(url_for("cart_view"))

    # --- AJOUT DU STL POUR LE VIEWER 3D ---
    if selected_activity and selected_activity.get("id"):
        stl_url = url_for("export_stl", activity_id=selected_activity["id"])
        print(f"🟠 [DEBUG] URL STL générée : {stl_url}")
    else:
        stl_url = None
        print("🔴 [DEBUG] Aucune activité -> pas d’URL STL")

    strava_just_disconnected = session.pop("strava_just_disconnected", False)

    print("✅ [DEBUG] Fin du traitement /gobelet.\n")
    return render_template(
        "gobelet.html",
        selected_activity=selected_activity,
        user=user,
        strava_connected=strava_connected,
        user_has_strava_linked=user_has_strava_linked,
        stl_url=stl_url,
    )


@app.route("/cart")
def cart_view():
    items = get_cart_items()
    total = cart_total()
    billing_data = session.get("billing_data")


    return render_template("cart.html", items=items, total=total, billing_data=billing_data)

@app.post("/remove-item/")
def remove_item():
    item_id = request.form.get("item_id")
    items = [it for it in get_cart_items() if it.get("id") != item_id]
    session["cart_items"] = items
    return redirect(url_for("cart_view"))

@app.post("/clear-cart")
def clear_cart():
    session["cart_items"] = []
    return redirect(url_for("cart_view"))

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        flash("Merci pour votre message !", "success")
        return redirect(url_for("contact"))
    return render_template("contact.html")


@app.route("/mentions")
def mentions():
    return render_template("mentions.html")

@app.route("/confidentialite")
def confidentialite():
    return render_template("confidentialite.html")

@app.route("/conditions")
def conditions():
    return render_template("conditions.html")

# ==========================================================
# 📦 Export STL direct (navigateur)
# ==========================================================
@app.route("/strava/export_stl/<activity_id>")
def export_stl(activity_id):
    try:
        stl_bytes = generate_stl_from_activity(activity_id, radius=1.0, target_max_size=100.0)
    except Exception as e:
        return Response(f"Erreur génération STL: {e}", status=400)

    filename = f"track_{activity_id}.stl"
    return Response(
        stl_bytes,
        mimetype="application/sla",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )

# === STL utils (Strava → STL) ===
import io, math
import trimesh
from flask import Response

def _latlon_to_mercator(lat, lon):
    R = 6378137.0
    x = math.radians(lon) * R
    y = math.log(math.tan(math.pi/4 + math.radians(lat)/2)) * R
    return x, y

def generate_stl_from_activity(activity_id: str, radius=1.0, target_max_size=100.0) -> bytes:
    """
    Récupère l'activité Strava via StravaService, décode le summary_polyline,
    crée un tracé 3D (segments cylindriques), normalise la taille, et renvoie un STL binaire (bytes).
    """
    # 1) Récup token session (user connecté ou guest)
    user, token = StravaService.get_token_from_session()
    if not token:
        raise RuntimeError("Non connecté à Strava")

    # 2) Fetch activité
    activity = StravaService.fetch_activity(token, activity_id)
    poly = (activity or {}).get("map", {}).get("summary_polyline")
    if not poly:
        raise RuntimeError("Pas de tracé GPS pour cette activité")

    coords = polyline.decode(poly)
    if not coords or len(coords) < 2:
        raise RuntimeError("Tracé insuffisant")

    # 3) Projection + mise à l’échelle initiale 2D → 3D (z=0)
    merc = [_latlon_to_mercator(lat, lon) for (lat, lon) in coords]
    xs, ys = zip(*merc)
    span_x, span_y = max(xs)-min(xs), max(ys)-min(ys)
    if span_x == 0 or span_y == 0:
        raise RuntimeError("Tracé dégénéré")

    # Mise à l’échelle “carte” ~ 600, puis normalisation plus tard
    scale_xy = min(600.0/span_x, 600.0/span_y)
    pts = [((x-min(xs))*scale_xy, (y-min(ys))*scale_xy, 0.0) for (x, y) in merc]

    # 4) Cylindres le long des segments
    segments = []
    for i in range(len(pts)-1):
        p1, p2 = pts[i], pts[i+1]
        # longueur
        h = math.dist(p1, p2)
        if h == 0:
            continue
        cyl = trimesh.creation.cylinder(radius=radius, height=h, sections=12)
        # positionner son centre au milieu du segment
        cyl.apply_translation([0, 0, h/2])

        # orienter vers le vecteur segment
        vec = [p2[j] - p1[j] for j in range(3)]
        cyl.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], vec))
        # déplacer au départ
        cyl.apply_translation(p1)
        segments.append(cyl)

    if not segments:
        raise RuntimeError("Pas assez de segments pour générer le mesh")

    mesh = trimesh.util.concatenate(segments)

    # 5) Normalisation: plus grande dimension = target_max_size (ex: 100 mm)
    max_extent = max(mesh.extents)
    if max_extent == 0:
        raise RuntimeError("Mesh vide")
    mesh.apply_scale(target_max_size / max_extent)

    # 6) Export STL binaire → bytes
    buf = io.BytesIO()
    mesh.export(buf, file_type="stl")  # binaire
    buf.seek(0)
    return buf.read()


@app.before_request
def check_session_timeout():
    now = datetime.utcnow()
    last_active = session.get("last_active_at")
    
    if last_active:
        last_active_dt = datetime.strptime(last_active, "%Y-%m-%d %H:%M:%S")
        if now - last_active_dt > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
            # Session expirée : supprimer toutes les infos de session
            session_keys_to_clear = ["user_id", "guest_billing", "cart_items", "last_order", "last_order_id", "last_active_at"]
            for key in session_keys_to_clear:
                session.pop(key, None)
            flash("Votre session a expiré. Veuillez recommencer votre commande.", "error")

            # Redirection selon le type d’utilisateur
            if "user_id" in session:
                return redirect(url_for("login"))  # utilisateur connecté
            else:
                return redirect(url_for("cart_view"))  # guest → panier

    # Mettre à jour la dernière activité si l'utilisateur ou guest est actif
    if "user_id" in session or "guest_billing" in session:
        session["last_active_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        

@app.before_request
def init_strava_stats():
    """
    Recalcule le compteur Strava dès le premier appel à l'application.
    Garantit la cohérence du compteur après un redémarrage Render.
    """
    try:
        total = StravaService.recalculate_connected_count()
        #print(f"✅ Compteur Strava recalculé au démarrage : {total}")
    except Exception as e:
        print(f"⚠️ Erreur au recalcul du compteur Strava au démarrage : {e}")


# ================================================== Functions ==================================================
# ----------------- Panier -----------------
"""def get_cart_items():
    items = session.get("cart_items")
    if items is None:
        items = []
        session["cart_items"] = items
    return items

def cart_total():
    items = get_cart_items()
    return round(sum(i.get("unit_price", 0.0) * i.get("qty", 1) for i in items), 2)"""

def add_cart_item(item):
    items = get_cart_items()
    for it in items:
        if (
            it.get("sku") == item.get("sku")
            and it.get("color") == item.get("color")
            and it.get("options") == item.get("options")
        ):
            it["qty"] += item["qty"]
            it["total"] = round(it["unit_price"] * it["qty"], 2)
            session["cart_items"] = items
            return
    items.append(item)
    session["cart_items"] = items





if __name__ == "__main__":
    with app.app_context():
        StravaService.recalculate_connected_count()
    # En local, utiliser un tunnel (ngrok/cloudflared) pour recevoir le webhook
    app.run(debug=True, host="127.0.0.1", port=5000)

