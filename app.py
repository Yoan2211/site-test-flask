# app.py
# -*- coding: utf-8 -*-
"""
@author: YA
To DO :
1) Windows Powershell: ouvrir un terminal dans le projet du site> ngrok config add-authtoken 32hpV03K5HSmz6ObDkYGhtsT78E_7Gx526uNHLqXeRwTCt6a2 OU ngrok config add-authtoken 34xxbMUscHVwv3jqZuk8itweQxX_3Z8dSDMbWQwX3fDtgprBu
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

from services.stl_manager import generate_stl_from_activity


# Logique métier (API, traitement, etc.)
from services.manage_sendgrid import envoyer_email_sendgrid_Client, envoyer_email_sendgrid_Admin, envoyer_email_contact_sendgrid
from services.kdrive import upload_to_kdrive
from services.strava_service import StravaService

import polyline

# Mollie SDK
from mollie.api.client import Client
from routes.mollie import get_cart_items, cart_total
from models.db_database import db, User, BillingInfo, Order, OrderPhoto, Stock



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

        # ✅ Calcul prix progressif selon le nombre d’options cochées
        unit = PRICES["BASE"]
        options_selected = []

        # Détecte les options activées
        if request.form.get("add_text") == "on":
            options_selected.append("text")
        if add_results:
            options_selected.append("results")
        if add_route:
            options_selected.append("route")

        # Vérifie qu'au moins une option est cochée
        if len(options_selected) == 0:
            flash("⚠️ Vous devez choisir au moins une option (texte, résultats ou tracé GPS).", "error")
            return redirect(url_for("gobelet"))

        # Calcule les suppléments progressifs
        if len(options_selected) >= 1:
            unit += 2.9
        if len(options_selected) >= 2:
            unit += 2.9
        if len(options_selected) >= 3:
            unit += 1.9

        total = round(unit * qty, 2)

        
        activity_id_opt = selected_activity["id"] if (selected_activity and selected_activity.get("id")) else None
        
        # 🆕 --- Texte personnalisé ---
        add_text = request.form.get("add_text") == "on"
        line1 = (request.form.get("custom_text_line1") or "").strip()
        line2 = (request.form.get("custom_text_line2") or "").strip()
        custom_text = ""

        if add_text:
            # Concatène les lignes valides avec saut de ligne
            custom_text = "\n".join([l for l in [line1, line2] if l]).strip()
            print(f"🟠 [DEBUG] Texte personnalisé détecté : '{custom_text}'")
        else:
            print("🔹 [DEBUG] Aucun texte personnalisé saisi.")

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
                # === Options texte ===
                "add_text": add_text,
                "custom_text_line1": line1,
                "custom_text_line2": line2,
                "custom_text": custom_text,
                # === Options Strava & autres ===
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

    stock = Stock.query.first()

    print("✅ [DEBUG] Fin du traitement /gobelet.\n")
    return render_template(
        "gobelet.html",
        selected_activity=selected_activity,
        user=user,
        strava_connected=strava_connected,
        user_has_strava_linked=user_has_strava_linked,
        stl_url=stl_url,
        stock=stock,
    )

@app.route("/cart")
def cart_view():
    items = get_cart_items()
    subtotal = cart_total()  # total sans frais
    shipping = 5.90 if items else 0.0  # frais uniquement s’il y a au moins un article
    total = subtotal + shipping
    billing_data = session.get("billing_data")

    return render_template(
        "cart.html",
        items=items,
        subtotal=subtotal,
        shipping=shipping,
        total=total,
        billing_data=billing_data
    )

@app.route("/api/order-status/<order_number>")
def api_order_status(order_number):
    order = Order.query.filter_by(order_number=order_number).first()
    if not order:
        return {"status": "unknown"}, 404
    return {"status": (order.status or "").lower()}

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
        name = request.form.get("name")
        email = request.form.get("email")
        message = request.form.get("message")

        if not all([name, email, message]):
            flash("Merci de remplir tous les champs.", "error")
            return redirect(url_for("contact"))

        if envoyer_email_contact_sendgrid(name, email, message):
            flash("Merci pour votre message ! Nous vous répondrons rapidement.", "success")
        else:
            flash("Une erreur est survenue lors de l'envoi du message.", "error")

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
from flask import Flask, render_template, request, redirect, url_for, session, Response
from services.manage_sendgrid import envoyer_email_sendgrid_test
from services.kdrive import upload_order_to_kdrive

from flask import Response, request


@app.route("/strava/export_stl/<activity_id>")
def export_stl(activity_id):
    """
    Exporte l'activité Strava sous forme de STL téléchargeable.
    """
    try:
        stl_bytes = generate_stl_from_activity(activity_id)
    except Exception as e:
        print(f"❌ Erreur génération STL : {e}")
        return Response(f"Erreur génération STL: {e}", status=400)

    filename = f"track_{activity_id}.stl"
    return Response(
        stl_bytes,
        mimetype="application/sla",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


from flask import Response, request, abort
from test_text_to_stl import generate_txt2stl
import hashlib, re, html


@app.route("/text/send_test_stl")
def send_test_stl():
    """
    Génère un STL à partir du texte fourni et retourne son hash.
    Ne fait plus aucun envoi d'email.
    Utilisable via :
      /text/send_test_stl?text=FINISHER+2025
      /text/send_test_stl?custom_text_line1=FINISHER&custom_text_line2=10KM
    """
    text = request.args.get("text", "").strip()
    line1 = request.args.get("custom_text_line1", "").strip()
    line2 = request.args.get("custom_text_line2", "").strip()

    # Combine les lignes si "text" est vide
    if not text:
        if line1 or line2:
            text = "\n".join([l for l in [line1, line2] if l]).strip()

    if not text:
        return "❌ Aucun texte fourni (ni text, ni custom_text_line1/line2)", 400

    try:
        print(f"🧱 [DEBUG] Génération STL test pour texte : '{text.replace(chr(10), ' / ')}'")
        stl_hash = generer_test_stl(text)
        escaped = html.escape(text)
        return f"✅ STL généré avec succès !<br>Texte : {escaped}<br>Hash STL : {stl_hash}"
    except Exception as e:
        print(f"❌ Erreur /text/send_test_stl : {e}")
        return f"❌ Erreur lors de la génération : {e}", 500



def generer_test_stl(text: str):
    """
    Génère un STL à partir d’un texte simple.
    Ne fait plus aucun envoi de mail.
    Retourne le hash SHA256 du fichier STL généré.
    """
    print("🧱 [DEBUG] Génération STL en local...")

    if not text.strip():
        raise ValueError("Texte vide — impossible de générer le STL.")

    # Génération du STL
    from test_text_to_stl import generate_txt2stl
    stl_bytes = generate_txt2stl(text)
    stl_hash = hashlib.sha256(stl_bytes).hexdigest()

    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", "_".join(text.splitlines()))[:50] or "text"
    print(f"📎 STL généré : {safe_name}.stl ({len(stl_bytes)} octets)")
    print(f"🔑 Hash SHA256 : {stl_hash}")

    return stl_hash



@app.route("/text/export_stl")
def export_text_stl():
    """
    Génère et renvoie un fichier STL téléchargeable depuis le texte fourni.
    Ne fait plus aucun envoi d'email.
    """
    text = request.args.get("text", "").strip()
    if not text:
        abort(400, "Aucun texte fourni")

    safe_name = "_".join(text.splitlines())[:50]

    try:
        stl_data = generate_txt2stl(text)
        print(f"✅ STL généré pour export : {safe_name}.stl ({len(stl_data)} octets)")
    except Exception as e:
        print("❌ Erreur STL :", e)
        abort(500, str(e))

    # 📦 Retour du STL au navigateur
    return Response(
        stl_data,
        mimetype="application/vnd.ms-pki.stl",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.stl"'}
    )



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

