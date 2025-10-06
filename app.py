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
from models.db_database import db, User, BillingInfo, Order, OrderPhoto



# Utils
from utils.tts_utils import format_pace_mmss, parse_time_to_seconds, make_public_asset_url, save_route_file
from utils.image_utils import render_track_image

import shutil

from __init__ import create_app
# ------------------------------------------------- Config -------------------------------------------------
# ----------------- Chargement app Flask & Routes -----------------
app = create_app()

# ----------------- Initialisation de la DB -----------------
with app.app_context():
    db.create_all()

# ----------------- Interface Admin -----------------
admin = Admin(app, name="RunCup Admin", template_mode="bootstrap3")

# Ajoute ton modèle User (tu pourras ajouter d’autres ensuite)
admin.add_view(ModelView(User, db.session))

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
    user = None  # par défaut
    strava_token = None  # token Strava disponible pour guest ou user
    strava_connected = False

    # --- Cas utilisateur connecté ---
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user:
            strava_token = StravaService.get_token(user)
            strava_connected = bool(strava_token)
        
            # ⚠️ Nouveau : flag pour savoir si l'utilisateur est lié à Strava
            user_has_strava_linked = bool(user.strava_access_token or user.strava_refresh_token)
        
            selected_activity = session.get("selected_activity") if strava_token else None
        else:
            session.pop("selected_activity", None)
            user_has_strava_linked = False
    else:
        # Guest
        token = session.get("strava_token")
        expires_at = session.get("strava_expires_at", 0)
        if token and time.time() < expires_at:
            strava_token = token
            strava_connected = True
            user_has_strava_linked = False
            selected_activity = session.get("selected_activity")
        else:
            session.pop("selected_activity", None)
            session.pop("strava_token", None)
            session.pop("strava_expires_at", None)
            user_has_strava_linked = False




    if request.method == "POST":
        color = request.form.get("color", "blanc")
        try:
            qty = max(1, int(request.form.get("qty", "1")))
        except ValueError:
            qty = 1

        # Résultats (si l’utilisateur coche ou si Strava pré-rempli)
        add_results = request.form.get("add_results") == "on" or bool(selected_activity)

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

            results_data = {
                "time": time_str,
                "distance": distance,
                "pace": pace,
            }

        # Tracé (si polyline importé de Strava)
        add_route = request.form.get("add_route") == "on" or ("polyline" in (selected_activity or {}))
        route_url = None
        route_local = None

        
        if add_route:
            if selected_activity and selected_activity.get("polyline"):
                coords = polyline.decode(selected_activity["polyline"])
                # Génère + sauvegarde dans static/imported_images/track_<id>.png
                render_track_image(coords, str(selected_activity["id"]))

                rel_path = f"imported_images/track_{selected_activity['id']}.png"  # relatif à /static
                route_local = os.path.join("static", rel_path)
                route_url = url_for("static", filename=rel_path)
            else:
                # Cas upload fichier utilisateur (si tu le gères)
                route_file = request.files.get("route_file")
                if route_file:
                    route_rel = save_route_file(route_file)     # ex "uploads/xxx.png"
                    route_url = make_public_asset_url(route_rel)
                    route_local = os.path.join("static", route_rel)



        # Prix
        unit = PRICES["BASE"]
        if add_results:
            unit += PRICES["RESULTS"]
        if add_route:
            unit += PRICES["ROUTE"]
        total = round(unit * qty, 2)

        # Item panier
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
                "route_local": route_local, # pour mail/D
                "from_strava": bool(selected_activity),
            }
        }

        add_cart_item(item)
        session.pop("selected_activity", None)  # vider après usage
        return redirect(url_for("cart_view"))

    strava_just_disconnected = session.pop("strava_just_disconnected", False)
    return render_template("gobelet.html", selected_activity=selected_activity, user=user, strava_connected=strava_connected,user_has_strava_linked=user_has_strava_linked)

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


# ----------------- Paiement via Mollie (TWINT) -----------------
# ------------------ Page checkout ------------------
@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    user = None
    billing_info = None

    # Récupérer les items du panier
    items = get_cart_items()
    if not items:
        flash("Le panier est vide.", "error")
        return redirect(url_for("cart_view"))

    total = cart_total()
    order_uuid = uuid.uuid4().hex  # seul identifiant de commande utilisé

    # Cas 1 : utilisateur connecté
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if not user:
            flash("Utilisateur introuvable.", "error")
            return redirect(url_for("cart_view"))  # pas /login pour ne pas bloquer guests

        if not user.billing_info:
            flash("Merci de remplir vos informations de facturation avant de payer.", "error")
            return redirect(url_for("checkout_info"))

        billing_info = user.billing_info

    # Cas 2 : guest
    else:
        if "guest_billing" not in session:
            flash("Merci de remplir vos informations de facturation avant de payer.", "error")
            return redirect(url_for("checkout_info"))

        billing_data = session["guest_billing"]

        # Créer un objet temporaire pour Mollie
        class TempBilling:
            def __init__(self, data):
                self.first_name = data.get("billing_firstname")
                self.last_name = data.get("billing_lastname")
                self.email = data.get("billing_email")
                self.street = data.get("billing_address")
                self.postal_code = data.get("billing_postal")
                self.city = data.get("billing_city")
                self.region = data.get("billing_canton")
                self.country = data.get("billing_country")
        billing_info = TempBilling(billing_data)

    # Préparer les données pour Mollie
    billing_data_mollie = {
        "givenName": billing_info.first_name,
        "familyName": billing_info.last_name,
        "email": billing_info.email,
        "streetAndNumber": billing_info.street,
        "postalCode": billing_info.postal_code,
        "city": billing_info.city,
        "region": billing_info.region,
        "country": billing_info.country
    }

    order_payload = {
        "amount": {"currency": "CHF", "value": f"{total:.2f}"},
        "orderNumber": order_uuid,
        "redirectUrl": url_for("payment_success", _external=True),
        "webhookUrl": WEBHOOK_URL,
        "metadata": {
            "order_id": order_uuid,
            "items": items,
            "total": total,
            "currency": "CHF",
        },
        "locale": "fr_CH",
        "method": "twint",
        "billingAddress": billing_data_mollie,
        "shippingAddress": billing_data_mollie,
        "lines": []
    }

    # Ajouter les lignes de commande
    for it in items:
        line = {
            "type": "physical",
            "sku": it.get("sku"),
            "name": it.get("name"),
            "quantity": it.get("qty"),
            "unitPrice": {"currency": "CHF", "value": f"{it.get('unit_price'):.2f}"},
            "totalAmount": {"currency": "CHF", "value": f"{it.get('total'):.2f}"},
            "vatRate": "0.00",
            "vatAmount": {"currency": "CHF", "value": "0.00"},
            "metadata": it.get("options", {}),
        }
        order_payload["lines"].append(line)

    # Envoyer la commande à Mollie
    try:
        response = requests.post(
            "https://api.mollie.com/v2/orders",
            json=order_payload,
            headers={"Authorization": f"Bearer {MOLLIE_API_KEY}"}
        )
        response.raise_for_status()
        mollie_order = response.json()
    except requests.exceptions.RequestException as e:
        flash(f"Erreur Mollie: {e}", "error")
        print("❌ Mollie error:", e)
        return redirect(url_for("cart_view"))

    checkout_url = mollie_order["_links"]["checkout"]["href"]

    # Stocker les infos de la dernière commande dans la session
    session["last_order"] = {
        "id": order_uuid,
        "line_items": items,
        "total": total,
        "currency": "CHF",
        "payment_url": checkout_url,
    }
    session["last_order_id"] = mollie_order["id"]

    return redirect(checkout_url)

# ------------------ Page infos de facturation ------------------
@app.route("/checkout-info_original", methods=["GET", "POST"])
def checkout_info_original():
    user = None
    billing_data = {}

    # Si l'utilisateur est connecté, récupérer ses infos
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user and user.billing_info:
            billing_data = {
                "billing_firstname": user.billing_info.first_name,
                "billing_lastname": user.billing_info.last_name,
                "billing_email": user.billing_info.email,
                "billing_address": user.billing_info.street,
                "billing_postal": user.billing_info.postal_code,
                "billing_city": user.billing_info.city,
                "billing_canton": user.billing_info.region,
                "billing_country": user.billing_info.country
            }

    if request.method == "POST":
        # Récupérer les données du formulaire
        billing_data = {
            "billing_firstname": request.form.get("billing_firstname", "").strip(),
            "billing_lastname": request.form.get("billing_lastname", "").strip(),
            "billing_email": request.form.get("billing_email", "").strip(),
            "billing_address": request.form.get("billing_address", "").strip(),
            "billing_postal": request.form.get("billing_postal", "").strip(),
            "billing_city": request.form.get("billing_city", "").strip(),
            "billing_canton": request.form.get("billing_canton", "").strip(),
            "billing_country": request.form.get("billing_country", "").strip()
        }

        # Validation basique
        if not billing_data["billing_email"] or not billing_data["billing_firstname"] or not billing_data["billing_lastname"]:
            flash("Merci de compléter vos informations de facturation.", "error")
            return redirect(url_for("checkout_info"))

        if user:
            if user.billing_info:
                # Mise à jour des colonnes existantes
                user.billing_info.first_name = billing_data["billing_firstname"]
                user.billing_info.last_name = billing_data["billing_lastname"]
                user.billing_info.email = billing_data["billing_email"]
                user.billing_info.street = billing_data.get("billing_address")
                user.billing_info.postal_code = billing_data.get("billing_postal")
                user.billing_info.city = billing_data.get("billing_city")
                user.billing_info.region = billing_data.get("billing_canton")
                user.billing_info.country = billing_data.get("billing_country")
            else:
                # Création d'un nouvel objet BillingInfo
                billing_info = BillingInfo(
                    user_id=user.id,
                    first_name=billing_data["billing_firstname"],
                    last_name=billing_data["billing_lastname"],
                    email=billing_data["billing_email"],
                    street=billing_data.get("billing_address"),
                    postal_code=billing_data.get("billing_postal"),
                    city=billing_data.get("billing_city"),
                    region=billing_data.get("billing_canton"),
                    country=billing_data.get("billing_country")
                )
                db.session.add(billing_info)

            db.session.commit()
        else:
            # Guest : stocker dans la session
            session['guest_billing'] = billing_data

        flash("Informations de facturation enregistrées.", "success")
        return redirect(url_for("checkout"))

    return render_template("checkout_info.html", user=user, billing_data=billing_data)

@app.route("/checkout-info", methods=["GET", "POST"])
def checkout_info():
    user = None
    billing_data = {}

    # --------------------------
    # 1️⃣ Identifier l'utilisateur
    # --------------------------
    if 'user_id' in session:
        user = User.query.get(session['user_id'])

        # Fusionner les infos guest si elles existent
        guest_data = session.pop('guest_billing', None)
        if guest_data:
            if user.billing_info:
                # Update des infos existantes
                for field, key in [
                    ("first_name", "billing_firstname"),
                    ("last_name", "billing_lastname"),
                    ("email", "billing_email"),
                    ("street", "billing_address"),
                    ("postal_code", "billing_postal"),
                    ("city", "billing_city"),
                    ("region", "billing_canton"),
                    ("country", "billing_country")
                ]:
                    val = guest_data.get(key)
                    if val:
                        setattr(user.billing_info, field, val)
            else:
                # Créer un BillingInfo à partir des infos guest
                billing_info = BillingInfo(
                    user_id=user.id,
                    first_name=guest_data.get("billing_firstname", ""),
                    last_name=guest_data.get("billing_lastname", ""),
                    email=guest_data.get("billing_email", ""),
                    street=guest_data.get("billing_address"),
                    postal_code=guest_data.get("billing_postal"),
                    city=guest_data.get("billing_city"),
                    region=guest_data.get("billing_canton"),
                    country=guest_data.get("billing_country")
                )
                db.session.add(billing_info)
            db.session.commit()

        # Charger les infos du user pour pré-remplir le formulaire
        if user.billing_info:
            billing_data = {
                "billing_firstname": user.billing_info.first_name,
                "billing_lastname": user.billing_info.last_name,
                "billing_email": user.billing_info.email,
                "billing_address": user.billing_info.street,
                "billing_postal": user.billing_info.postal_code,
                "billing_city": user.billing_info.city,
                "billing_canton": user.billing_info.region,
                "billing_country": user.billing_info.country
            }

    # --------------------------
    # 2️⃣ Sinon, vérifier guest
    # --------------------------
    elif "guest_billing" in session:
        billing_data = session["guest_billing"]

    # --------------------------
    # 3️⃣ POST: sauvegarder les infos
    # --------------------------
    if request.method == "POST":
        billing_data = {
            "billing_firstname": request.form.get("billing_firstname", "").strip(),
            "billing_lastname": request.form.get("billing_lastname", "").strip(),
            "billing_email": request.form.get("billing_email", "").strip(),
            "billing_address": request.form.get("billing_address", "").strip(),
            "billing_postal": request.form.get("billing_postal", "").strip(),
            "billing_city": request.form.get("billing_city", "").strip(),
            "billing_canton": request.form.get("billing_canton", "").strip(),
            "billing_country": request.form.get("billing_country", "").strip()
        }

        # Validation simple
        if not billing_data["billing_email"] or not billing_data["billing_firstname"] or not billing_data["billing_lastname"]:
            flash("Merci de compléter vos informations de facturation.", "error")
            return redirect(url_for("checkout_info"))

        # --------------------------
        # 4️⃣ User connecté → update/create BillingInfo
        # --------------------------
        if user:
            if user.billing_info:
                for field, key in [
                    ("first_name", "billing_firstname"),
                    ("last_name", "billing_lastname"),
                    ("email", "billing_email"),
                    ("street", "billing_address"),
                    ("postal_code", "billing_postal"),
                    ("city", "billing_city"),
                    ("region", "billing_canton"),
                    ("country", "billing_country")
                ]:
                    setattr(user.billing_info, field, billing_data[key])
            else:
                billing_info = BillingInfo(
                    user_id=user.id,
                    first_name=billing_data["billing_firstname"],
                    last_name=billing_data["billing_lastname"],
                    email=billing_data["billing_email"],
                    street=billing_data.get("billing_address"),
                    postal_code=billing_data.get("billing_postal"),
                    city=billing_data.get("billing_city"),
                    region=billing_data.get("billing_canton"),
                    country=billing_data.get("billing_country")
                )
                db.session.add(billing_info)
            db.session.commit()

        # --------------------------
        # 5️⃣ Guest → stocker dans session
        # --------------------------
        else:
            session['guest_billing'] = billing_data

        flash("Informations de facturation enregistrées.", "success")
        return redirect(url_for("checkout"))

    return render_template("checkout_info.html", user=user, billing_data=billing_data)


@app.route("/webhook", methods=["POST"])
def mollie_webhook():
    mollie_order_id = request.form.get("id")
    if not mollie_order_id:
        return "ID Mollie manquant", 400

    try:
        # 1️⃣ Récupération de la commande Mollie
        response = requests.get(
            f"https://api.mollie.com/v2/orders/{mollie_order_id}",
            headers={"Authorization": f"Bearer {MOLLIE_API_KEY}"}
        )
        response.raise_for_status()
        order_data = response.json()
        status = order_data.get("status")

        # 2️⃣ Récupération du metadata
        metadata = order_data.get("metadata", {})
        internal_order_id = metadata.get("order_id")
        items = metadata.get("items", [])
        email_client = order_data.get("billingAddress", {}).get("email")

        if not internal_order_id:
            return "Erreur: internal_order_id manquant", 400

        # 3️⃣ Vérifier ou créer l'utilisateur
        billing = order_data.get("billingAddress", {})
        user = None
        if email_client:
            user = User.query.filter_by(email=email_client).first()
            if not user:
                user = User(
                    first_name=billing.get("givenName", ""),
                    last_name=billing.get("familyName", ""),
                    email=email_client,
                    password=None  # guest, pas de mot de passe
                )
                db.session.add(user)
                db.session.commit()
                print(f"👤 Utilisateur guest créé : {email_client}")

        # 4️⃣ Vérifier si la commande existe
        order = Order.query.filter_by(order_number=internal_order_id).first()
        if not order:
            # Nouvelle commande
            order = Order(
                order_number=internal_order_id,
                user_id=user.id if user else None,
                amount=float(order_data["amount"]["value"]),
                currency=order_data["amount"]["currency"],
                status=status,
                payment_date=datetime.utcnow() if status == "paid" else None,
                billing_first_name=billing.get("givenName", ""),
                billing_last_name=billing.get("familyName", ""),
                billing_email=email_client,
                billing_street=billing.get("streetAndNumber", ""),
                billing_postal_code=billing.get("postalCode", ""),
                billing_city=billing.get("city", ""),
                billing_region=billing.get("region", ""),
                billing_country=billing.get("country", "CH"),
                processed=False,
            )
            db.session.add(order)
            db.session.commit()
            print(f"✅ Commande {internal_order_id} créée.")
        else:
            # Mettre à jour le statut et la date de paiement si nécessaire
            if status == "paid" and not order.payment_date:
                order.payment_date = datetime.utcnow()
            order.status = status
            db.session.commit()
            print(f"🔄 Commande {internal_order_id} existante mise à jour : {status}")

        # 5️⃣ Ne traiter que si payé et non encore traité
        if status != "paid":
            print(f"⚠️ Commande {internal_order_id} pas encore payée ({status}), on ignore.")
            return "Commande non payée", 200

        if order.processed:
            print(f"🔁 Commande {internal_order_id} déjà traitée, on ignore.")
            return "Déjà traité", 200

        # 6️⃣ Export du fichier .txt
        os.makedirs("exports", exist_ok=True)
        txt_path = f"exports/commande_{internal_order_id}.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(order_data, indent=4, ensure_ascii=False))

        """# 7️⃣ Gestion images (route)
        image_path = None
        for item in items:
            opts = item.get("options", {})
            if opts.get("add_route") and opts.get("route_url"):
                filename = opts["route_url"].split("/")[-1]
                src = f"static/uploads/{filename}"
                dst = f"imported_images/{filename}"
                os.makedirs("imported_images", exist_ok=True)
                if os.path.exists(src):
                    with open(src, "rb") as s, open(dst, "wb") as d:
                        d.write(s.read())
                    image_path = dst
                    photo = OrderPhoto(order_id=order.id, photo_url=image_path)
                    db.session.add(photo)
                    db.session.commit()
                    print("🖼️ Image copiée et enregistrée en DB.")"""

        
        image_path = None
        for item in items:
            opts = item.get("options", {})
            if opts.get("add_route"):
                if opts.get("route_local") and os.path.exists(opts["route_local"]):
                    src = opts["route_local"]
                elif opts.get("route_url") and "/static/" in opts["route_url"]:
                    # Reconstruit le chemin disque à partir de l’URL
                    filename = opts["route_url"].split("/")[-1]
                    if "imported_images" in opts["route_url"]:
                        src = os.path.join("static", "imported_images", filename)
                    else:
                        src = os.path.join("static", "uploads", filename)
                else:
                    src = None

                if src and os.path.exists(src):
                    os.makedirs("imported_images", exist_ok=True)
                    dst = os.path.join("imported_images", os.path.basename(src))
                    shutil.copyfile(src, dst)
                    image_path = dst

                    photo = OrderPhoto(order_id=order.id, photo_url=image_path)
                    db.session.add(photo)
                    db.session.commit()
                    print("🖼️ Image copiée et enregistrée en DB.")



        # 8️⃣ Préparer dict pour emails
        order_view = {
            "id": order.order_number,
            "currency": order.currency,
            "total": float(order.amount),
            "line_items": items,
            "billingAddress": billing
        }

        # 9️⃣ Envoi emails
        if email_client:
            envoyer_email_sendgrid_Client(order=order_view, destinataire=email_client)
        envoyer_email_sendgrid_Admin(order=order_view, destinataire="stravacup@gmail.com", txt_path=txt_path, image_path=image_path)

        # 🔟 Upload Google Drive
        try:
            upload_to_google_drive_cmdFile(txt_path, os.path.basename(txt_path), internal_order_id)
            if image_path and os.path.exists(image_path):
                upload_to_google_drive_cmdFile(image_path, os.path.basename(image_path), internal_order_id)
        except Exception as e:
            print(f"⚠️ Erreur upload Google Drive : {e}")

        # 11️⃣ Marquer comme traité
        order.processed = True
        db.session.commit()
        print(f"✅ Commande {internal_order_id} traitée avec succès")

        return "Webhook traité", 200

    except requests.exceptions.RequestException as e:
        print(f"❌ Erreur Mollie webhook: {e}")
        return f"Erreur Mollie: {e}", 500
    except Exception as e:
        print(f"❌ Erreur inattendue webhook: {e}")
        return f"Erreur interne: {e}", 500

@app.route("/payment/success", methods=["GET"])
def payment_success():
    order = session.get("last_order")
    if not order:
        flash("Commande introuvable.", "error")
        return redirect(url_for("cart_view"))

    order_id = session.get("last_order_id")
    status = session.get("last_order_status", "unknown")

    if order_id:
        try:
            mollie_order = mollie_client.orders.get(order_id)
            status = mollie_order.status
            session["last_order_status"] = status  # sync avec webhook
        except Exception as e:
            flash(f"Erreur lors de la vérification de la commande : {e}", "error")
            return render_template("failure.html", order=order, status="erreur")

    if status == "paid":
        flash("Commande payée ✅ Merci pour votre achat !", "success")
        session["cart_items"] = []
        return render_template("success.html", order=order)

    flash(f"Paiement non réussi (statut : {status})", "error")
    return render_template("failure.html", order=order, status=status)

@app.get("/webhook/ping")
def webhook_ping():
    return "pong", 200





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
def restrict_admin():
    if request.path.startswith("/admin"):
        # Vérifie que l'IP du visiteur correspond à celle autorisée
        if request.remote_addr != ALLOWED_IP:
            return redirect(url_for("auth_bp.login"))  # ou "/" si tu veux juste bloquer

# ================================================== Functions ==================================================
# ----------------- Panier -----------------
def get_cart_items():
    items = session.get("cart_items")
    if items is None:
        items = []
        session["cart_items"] = items
    return items

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

def cart_total():
    items = get_cart_items()
    return round(sum(i.get("unit_price", 0.0) * i.get("qty", 1) for i in items), 2)



if __name__ == "__main__":

    # En local, utiliser un tunnel (ngrok/cloudflared) pour recevoir le webhook
    app.run(debug=True, host="127.0.0.1", port=5000)

