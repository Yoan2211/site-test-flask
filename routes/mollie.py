"""
mollie.py — Gestion des paiements Mollie + TWINT personnel temporaire
"""

from flask import Blueprint, current_app, session, request, url_for, redirect, render_template, flash
from datetime import datetime
import uuid
import os
import json
import requests
import shutil

from models.db_database import db, User, BillingInfo, Order, OrderPhoto
from services.manage_sendgrid import envoyer_email_sendgrid_Client, envoyer_email_sendgrid_Admin
from services.googledrive import upload_to_google_drive_cmdFile

mollie_bp = Blueprint("mollie", __name__)

# ----------------- Helpers -----------------
def get_cart_items():
    items = session.get("cart_items", [])
    session["cart_items"] = items
    return items

def cart_total():
    items = get_cart_items()
    return round(sum(i.get("unit_price", 0.0) * i.get("qty", 1) for i in items), 2)


@mollie_bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    """Crée une commande Mollie (ordre) et redirige vers l'URL de checkout.
    Lit la config depuis current_app.config.
    """
    # Config
    MOLLIE_API_KEY = current_app.config.get("MOLLIE_API_KEY")
    WEBHOOK_URL = current_app.config.get("WEBHOOK_URL")
    PRICES = current_app.config.get("PRICES", {})

    # Récupérer les items du panier
    items = get_cart_items()
    if not items:
        flash("Le panier est vide.", "error")
        return redirect(url_for("cart_view"))

    total = cart_total()
    order_uuid = uuid.uuid4().hex

    # Récupérer l'utilisateur / billing
    user = None
    billing_info = None
    if "user_id" in session:
        user = User.query.get(session["user_id"]) if session.get("user_id") else None
        if not user:
            flash("Utilisateur introuvable.", "error")
            return redirect(url_for("cart_view"))
        if not user.billing_info:
            flash("Merci de remplir vos informations de facturation avant de payer.", "error")
            return redirect(url_for("checkout_info"))
        billing_info = user.billing_info
    else:
        if "guest_billing" not in session:
            flash("Merci de remplir vos informations de facturation avant de payer.", "error")
            return redirect(url_for("checkout_info"))

        billing_data = session["guest_billing"]

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

    # Préparer payload order
    billing_data_mollie = {
        "givenName": billing_info.first_name,
        "familyName": billing_info.last_name,
        "email": billing_info.email,
        "streetAndNumber": getattr(billing_info, 'street', ''),
        "postalCode": getattr(billing_info, 'postal_code', ''),
        "city": getattr(billing_info, 'city', ''),
        "region": getattr(billing_info, 'region', ''),
        "country": getattr(billing_info, 'country', 'CH')
    }

    order_payload = {
        "amount": {"currency": "CHF", "value": f"{total:.2f}"},
        "orderNumber": order_uuid,
        "redirectUrl": url_for("mollie.payment_success", _external=True),
        "webhookUrl": WEBHOOK_URL,
        "metadata": {"order_id": order_uuid, "items": items, "total": total, "currency": "CHF"},
        "locale": "fr_CH",
        # Choix méthode laissé à la logique originelle (twint)
        #"method": "twint",
        "billingAddress": billing_data_mollie,
        "shippingAddress": billing_data_mollie,
        "lines": []
    }

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

    # Appel Mollie
    try:
        response = requests.post("https://api.mollie.com/v2/orders", json=order_payload,
                                 headers={"Authorization": f"Bearer {MOLLIE_API_KEY}"})
        response.raise_for_status()
        mollie_order = response.json()
    except requests.exceptions.RequestException as e:
        flash(f"Erreur Mollie: {e}", "error")
        current_app.logger.exception("Erreur Mollie checkout")
        return redirect(url_for("cart_view"))

    checkout_url = mollie_order["_links"]["checkout"]["href"]

    # Stocker dans la session
    session["last_order"] = {"id": order_uuid, "line_items": items, "total": total, "currency": "CHF", "payment_url": checkout_url}
    session["last_order_id"] = mollie_order["id"]

    return redirect(checkout_url)


# ----------------- Route: checkout-info -----------------
@mollie_bp.route("/checkout-info", methods=["GET", "POST"])
def checkout_info():
    user = None
    billing_data = {}

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

    elif "guest_billing" in session:
        billing_data = session["guest_billing"]

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

        if not billing_data["billing_email"] or not billing_data["billing_firstname"] or not billing_data["billing_lastname"]:
            flash("Merci de compléter vos informations de facturation.", "error")
            return redirect(url_for("mollie.checkout_info"))

        if user:
            if user.billing_info:
                for field, key in [("first_name", "billing_firstname"), ("last_name", "billing_lastname"),
                                   ("email", "billing_email"), ("street", "billing_address"),
                                   ("postal_code", "billing_postal"), ("city", "billing_city"),
                                   ("region", "billing_canton"), ("country", "billing_country")]:
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
        else:
            session['guest_billing'] = billing_data

        flash("Informations de facturation enregistrées.", "success")
        return redirect(url_for("mollie.checkout"))

    return render_template("checkout_info.html", user=user, billing_data=billing_data)


# ----------------- Route: Mollie webhook -----------------
@mollie_bp.route("/webhook", methods=["POST"])
def mollie_webhook():
    MOLLIE_API_KEY = current_app.config.get("MOLLIE_API_KEY")

    mollie_order_id = request.form.get("id")
    if not mollie_order_id:
        return "ID Mollie manquant", 400

    try:
        response = requests.get(f"https://api.mollie.com/v2/orders/{mollie_order_id}", headers={"Authorization": f"Bearer {MOLLIE_API_KEY}"})
        response.raise_for_status()
        order_data = response.json()
        status = order_data.get("status")

        metadata = order_data.get("metadata", {})
        internal_order_id = metadata.get("order_id")
        items = metadata.get("items", [])
        email_client = order_data.get("billingAddress", {}).get("email")

        if not internal_order_id:
            return "Erreur: internal_order_id manquant", 400

        billing = order_data.get("billingAddress", {})
        user = None
        if email_client:
            user = User.query.filter_by(email=email_client).first()
            if not user:
                user = User(first_name=billing.get("givenName", ""), last_name=billing.get("familyName", ""), email=email_client, password=None)
                db.session.add(user)
                db.session.commit()

        order = Order.query.filter_by(order_number=internal_order_id).first()
        if not order:
            order = Order(order_number=internal_order_id, user_id=user.id if user else None,
                          amount=float(order_data["amount"]["value"]), currency=order_data["amount"]["currency"],
                          status=status,
                          payment_date=datetime.utcnow() if status == "paid" else None,
                          billing_first_name=billing.get("givenName", ""), billing_last_name=billing.get("familyName", ""),
                          billing_email=email_client, billing_street=billing.get("streetAndNumber", ""), billing_postal_code=billing.get("postalCode", ""),
                          billing_city=billing.get("city", ""), billing_region=billing.get("region", ""), billing_country=billing.get("country", "CH"), processed=False)
            db.session.add(order)
            db.session.commit()
        else:
            if status == "paid" and not order.payment_date:
                order.payment_date = datetime.utcnow()
            order.status = status
            db.session.commit()

        if status != "paid":
            current_app.logger.info(f"Commande {internal_order_id} pas encore payée ({status})")
            return "Commande non payée", 200

        if order.processed:
            current_app.logger.info(f"Commande {internal_order_id} déjà traitée")
            return "Déjà traité", 200

        # Export JSON local
        os.makedirs("exports", exist_ok=True)
        txt_path = f"exports/commande_{internal_order_id}.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(order_data, indent=4, ensure_ascii=False))

        # Gestion images
        image_path = None
        for item in items:
            opts = item.get("options", {})
            if opts.get("add_route"):
                if opts.get("route_local") and os.path.exists(opts["route_local"]):
                    src = opts["route_local"]
                elif opts.get("route_url") and "/static/" in opts.get("route_url", ""):
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

        order_view = {"id": order.order_number, "currency": order.currency, "total": float(order.amount), "line_items": items, "billingAddress": billing}

        if email_client:
            envoyer_email_sendgrid_Client(order=order_view, destinataire=email_client)
        envoyer_email_sendgrid_Admin(order=order_view, destinataire=current_app.config.get("ADMIN_EMAIL", "stravacup@gmail.com"), txt_path=txt_path, image_path=image_path)

        try:
            upload_to_google_drive_cmdFile(txt_path, os.path.basename(txt_path), internal_order_id)
            if image_path and os.path.exists(image_path):
                upload_to_google_drive_cmdFile(image_path, os.path.basename(image_path), internal_order_id)
        except Exception:
            current_app.logger.exception("Erreur upload Google Drive")

        order.processed = True
        db.session.commit()

        return "Webhook traité", 200

    except requests.exceptions.RequestException as e:
        current_app.logger.exception("Erreur Mollie webhook")
        return f"Erreur Mollie: {e}", 500
    except Exception as e:
        current_app.logger.exception("Erreur inattendue webhook")
        return f"Erreur interne: {e}", 500


# ----------------- Route : Success -----------------
@mollie_bp.route("/payment/success")
def payment_success():
    """Page de confirmation après retour Mollie."""
    order = session.get("last_order")
    if not order:
        flash("Commande introuvable.", "error")
        return redirect(url_for("cart_view"))
    flash("Commande payée ✅ Merci pour votre achat !", "success")
    session["cart_items"] = []
    return render_template("success.html", order=order)
