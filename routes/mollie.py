"""
mollie.py — Gestion des paiements Mollie + TWINT personnel temporaire
"""

from flask import Blueprint, current_app, session, request, url_for, redirect, render_template, flash
from datetime import datetime
import uuid
import os
import io
import json
import requests
import shutil

from utils.tts_utils import extract_custom_text

from models.db_database import db, User, BillingInfo, Order, OrderPhoto, Stock
from services.manage_sendgrid import envoyer_email_sendgrid_Client, envoyer_email_sendgrid_Admin
from services.googledrive import upload_to_google_drive_cmdFile

from services.strava_service import StravaService

import math, polyline, trimesh
from shapely.geometry import LineString
from shapely.ops import unary_union

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
    """Crée une commande Mollie (ordre) et redirige vers l'URL de checkout."""
    MOLLIE_API_KEY = current_app.config.get("MOLLIE_API_KEY")
    WEBHOOK_URL = current_app.config.get("WEBHOOK_URL")

    # 🛒 Récupérer les items du panier
    items = get_cart_items()
    if not items:
        flash("Le panier est vide.", "error")
        return redirect(url_for("cart_view"))

    # 💰 Calcul du total avec frais de livraison
    subtotal = cart_total()
    shipping_fee = 5.90
    total = subtotal + shipping_fee

    order_uuid = uuid.uuid4().hex

    # 👤 Récupérer l'utilisateur ou invité
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

    # 🧾 Conversion des données pour Mollie
    billing_data_mollie = {
        "givenName": billing_info.first_name,
        "familyName": billing_info.last_name,
        "email": billing_info.email,
        "streetAndNumber": getattr(billing_info, "street", ""),
        "postalCode": getattr(billing_info, "postal_code", ""),
        "city": getattr(billing_info, "city", ""),
        "region": getattr(billing_info, "region", ""),
        "country": getattr(billing_info, "country", "CH"),
    }

    # 🧱 Payload de commande Mollie
    order_payload = {
        "amount": {"currency": "CHF", "value": f"{total:.2f}"},
        "orderNumber": order_uuid,
        "redirectUrl": url_for("mollie.payment_success", _external=True),
        "webhookUrl": WEBHOOK_URL,
        "metadata": {
            "order_id": order_uuid,
            "items": items,
            "subtotal": subtotal,
            "shipping": shipping_fee,
            "total": total,
            "currency": "CHF",
            "guest_id": session.get("guest_id"),
            "user_id": session.get("user_id"),
        },
        "locale": "fr_CH",
        "billingAddress": billing_data_mollie,
        "shippingAddress": billing_data_mollie,
        "lines": [],
    }

    # ➕ Lignes produits
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

    # 🚚 Ligne supplémentaire pour les frais de livraison
    order_payload["lines"].append({
        "name": "Frais de livraison",
        "type": "shipping_fee",
        "quantity": 1,
        "unitPrice": {"currency": "CHF", "value": f"{shipping_fee:.2f}"},
        "totalAmount": {"currency": "CHF", "value": f"{shipping_fee:.2f}"},
        "vatRate": "0.00",
        "vatAmount": {"currency": "CHF", "value": "0.00"},
    })

    # 💾 Création de la commande en base avant Mollie
    from models.db_database import Order
    try:
        existing_order = Order.query.filter_by(order_number=order_uuid).first()
        if not existing_order:
            new_order = Order(
                order_number=order_uuid,
                user_id=session.get("user_id"),
                amount=total,
                currency="CHF",
                status="pending",
                billing_first_name=billing_info.first_name,
                billing_last_name=billing_info.last_name,
                billing_email=billing_info.email,
                billing_street=getattr(billing_info, "street", ""),
                billing_postal_code=getattr(billing_info, "postal_code", ""),
                billing_city=getattr(billing_info, "city", ""),
                billing_region=getattr(billing_info, "region", ""),
                billing_country=getattr(billing_info, "country", "CH"),
                processed=False,
            )
            db.session.add(new_order)
            db.session.commit()
            current_app.logger.info(f"🧾 Commande temporaire créée : {order_uuid}")
    except Exception as e:
        current_app.logger.exception(f"Erreur création commande en base avant Mollie : {e}")

    # 🔗 Création réelle via l’API Mollie
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
        current_app.logger.exception("Erreur Mollie checkout")
        return redirect(url_for("cart_view"))

    checkout_url = mollie_order["_links"]["checkout"]["href"]

    # 🧠 Stocker en session
    session["last_order"] = {
        "id": order_uuid,
        "line_items": items,
        "subtotal": subtotal,
        "shipping": shipping_fee,
        "total": total,
        "currency": "CHF",
        "payment_url": checkout_url,
    }
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
        # 🔎 1️⃣ Récupération de la commande Mollie
        response = requests.get(
            f"https://api.mollie.com/v2/orders/{mollie_order_id}",
            headers={"Authorization": f"Bearer {MOLLIE_API_KEY}"}
        )
        response.raise_for_status()
        order_data = response.json()
        status = order_data.get("status")
        metadata = order_data.get("metadata", {})
        internal_order_id = metadata.get("order_id")
        mode = order_data.get("mode", "live")
        print("📦 [DEBUG] Metadata reçu de Mollie :", json.dumps(order_data.get("metadata", {}), indent=2))

        # -------------------------------------------------------------
        # 🔒 1️⃣ Vérification cohérence commande ↔ base de données
        # -------------------------------------------------------------
        order = Order.query.filter_by(order_number=internal_order_id).first()

        if not order:
            current_app.logger.warning(
                f"🚨 Webhook ignoré : aucune commande trouvée avec order_number={internal_order_id} "
                f"pour Mollie ID {mollie_order_id}"
            )
            return "Commande inconnue", 400

        # Si déjà traitée, on stoppe
        if order.processed:
            current_app.logger.info(f"Webhook ignoré : commande {internal_order_id} déjà traitée.")
            return "Déjà traité", 200

        # Si incohérence Mollie ID → stoppe aussi
        if order.mollie_id and order.mollie_id != mollie_order_id:
            current_app.logger.warning(
                f"⚠️ Incohérence Mollie ID : commande {order.order_number} "
                f"liée à {order.mollie_id}, reçu {mollie_order_id}"
            )
            return "Mollie ID non conforme", 400

        # ---------------------------------------------------------
        # 🔒 Vérification renforcée du statut réel côté Mollie
        # ---------------------------------------------------------
        is_paid = False
        payments_link = order_data.get("_links", {}).get("payments", {}).get("href")

        if status == "paid" and payments_link:
            payments_resp = requests.get(
                payments_link,
                headers={"Authorization": f"Bearer {MOLLIE_API_KEY}"}
            )
            if payments_resp.status_code == 200:
                payments_data = payments_resp.json()
                payments_list = payments_data.get("data", [])
                for p in payments_list:
                    if p.get("status") == "paid" and p.get("paidAt"):
                        is_paid = True
                        break

        # ✅ Tolère les paiements de test (pas de paidAt en sandbox)
        if mode == "test" and status == "paid":
            is_paid = True
            current_app.logger.info(f"🧪 Paiement TEST accepté pour commande {internal_order_id}")

        # 🚫 Bloque les paiements “paid” sans preuve réelle en live
        if mode == "live" and status == "paid" and not is_paid:
            current_app.logger.warning(
                f"🚫 Paiement fantôme ignoré en mode live (status='paid' sans preuve de paiement) — ID {mollie_order_id}"
            )
            return "Fake paid ignored", 200

        if status != "paid" or not is_paid:
            current_app.logger.info(f"⏸ Commande {internal_order_id} ignorée (status={status})")
            return "Commande non payée", 200

        # ----------------------------------------------------
        # ✅ 5️⃣ Paiement confirmé — mise à jour ou création
        # ----------------------------------------------------
        # Gestion du Stock - Compteur + 1
        stock = Stock.query.first()
        if stock.current_orders < stock.max_orders:
            stock.current_orders += 1
            db.session.commit()
            current_app.logger.info(f"✅ Commande validée. Stock restant : {stock.remaining()}")

        guest_id = metadata.get("guest_id")
        user_id = metadata.get("user_id")
        items = metadata.get("items", [])
        email_client = order_data.get("billingAddress", {}).get("email")
        billing = order_data.get("billingAddress", {})

        # Associe l'ID Mollie et met à jour les infos
        order.mollie_id = mollie_order_id
        order.status = "paid"
        order.payment_date = datetime.utcnow()
        order.billing_email = email_client or order.billing_email
        db.session.commit()

        # ----------------------------------------------------
        # 🧾 Export JSON local (sauvegarde)
        # ----------------------------------------------------
        os.makedirs("exports", exist_ok=True)
        txt_path = f"exports/commande_{internal_order_id}.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(order_data, indent=4, ensure_ascii=False))

        # ----------------------------------------------------
        # 🧮 Construction de l’objet order_view pour les emails
        # ----------------------------------------------------
        root_metadata = order_data.get("metadata", {}) or {}
        billing = order_data.get("billingAddress", {}) or {}
        items = root_metadata.get("items", [])

        # 🔒 Récupération sûre des totaux
        subtotal = float(root_metadata.get("subtotal", 0.0))
        shipping = float(root_metadata.get("shipping", 0.0))
        total = float(root_metadata.get("total", subtotal + shipping))

        # 🔍 Si shipping toujours 0 → recalcul de secours depuis lines
        if shipping == 0 and order_data.get("lines"):
            for line in order_data["lines"]:
                name = line.get("name", "").lower()
                amount_val = float(line.get("totalAmount", {}).get("value", 0))
                if "livraison" in name or "shipping" in name:
                    shipping += amount_val
                else:
                    subtotal += amount_val
            total = subtotal + shipping

        order_view = {
            "id": order.order_number,
            "currency": order.currency or "CHF",
            "subtotal": round(subtotal, 2),
            "shipping": round(shipping, 2),
            "total": round(total, 2),
            "line_items": items,
            "billingAddress": billing,
            "activity_id": root_metadata.get("activity_id"),
            "user_id": root_metadata.get("user_id"),
            "guest_id": root_metadata.get("guest_id"),
            "options": {
                "activity_id": root_metadata.get("activity_id"),
            },
        }


        # =====================================================
        # 📤 UPLOAD DE LA COMMANDE SUR KDRIVE
        # =====================================================
        try:
            from services.kdrive import upload_order_to_kdrive
            txt_bytes = json.dumps(order_data, indent=4, ensure_ascii=False).encode("utf-8")
            stl_bytes = None
            upload_order_to_kdrive(
                order_number=internal_order_id,
                txt_bytes=txt_bytes,
                stl_bytes=stl_bytes,
            )
            current_app.logger.info(f"✅ Commande {internal_order_id} transférée avec succès sur kDrive.")
        except Exception as e:
            current_app.logger.exception(f"❌ Erreur upload kDrive : {e}")

        # ----------------------------------------------------
        # ✅ 6️⃣ Marquer la commande comme traitée
        # ----------------------------------------------------
        order.processed = True
        db.session.commit()
        current_app.logger.info(f"✅ Webhook finalisé pour commande {internal_order_id}")

        # ----------------------------------------------------
        # ✉️ Envoi des emails (client + admin)
        # ----------------------------------------------------
        if email_client:
            # 🔄 Fusion des options du premier article
            if order_view.get("line_items"):
                first_item = order_view["line_items"][0]
                opts = first_item.get("options", {}) or {}
                order_view["options"].update({
                    k: v for k, v in opts.items()
                    if v and k not in order_view["options"]
                })

            # 🔒 Patch texte personnalisé
            text = extract_custom_text(order_view)
            if text and not order_view["options"].get("custom_text"):
                order_view["options"]["custom_text"] = text

            # ✉️ Envoi
            print("🔎 [DEBUG] order_view envoyé au client =", json.dumps(order_view, indent=2))

            envoyer_email_sendgrid_Client(order=order_view, destinataire=email_client)
            envoyer_email_sendgrid_Admin(
                order=order_view,
                destinataire="stravacup@gmail.com",
                txt_path=txt_path
            )


    except requests.exceptions.RequestException as e:
        current_app.logger.exception("Erreur Mollie webhook")
        return f"Erreur Mollie: {e}", 500
    except Exception as e:
        current_app.logger.exception("Erreur inattendue webhook")
        return f"Erreur interne: {e}", 500

    return "OK", 200


# ----------------- Route : Success -----------------
@mollie_bp.route("/payment/success")
def payment_success():
    """Page de confirmation après retour Mollie."""
    from flask import current_app
    import requests

    order = session.get("last_order")
    if not order:
        flash("Commande introuvable.", "error")
        return redirect(url_for("cart_view"))

    mollie_order_id = order.get("mollie_order_id")

    if not mollie_order_id:
        #flash("Commande payée mais ID Mollie manquant.", "warning")
        order["billing_email"] = None
        session["cart_items"] = []
        return render_template("success.html", order=order)

    try:
        # 🔎 Récupérer les infos depuis Mollie
        MOLLIE_API_KEY = current_app.config.get("MOLLIE_API_KEY")
        r = requests.get(
            f"https://api.mollie.com/v2/orders/{mollie_order_id}",
            headers={"Authorization": f"Bearer {MOLLIE_API_KEY}"}
        )
        r.raise_for_status()
        mollie_data = r.json()

        # 💰 Montant total
        amount = mollie_data.get("amount", {}).get("value", "0.00")
        currency = mollie_data.get("amount", {}).get("currency", "EUR")

        order["total"] = float(amount)
        order["currency"] = currency

        # 📦 Lignes d’articles
        if mollie_data.get("lines"):
            order["line_items"] = [
                {
                    "name": l.get("name"),
                    "qty": l.get("quantity"),
                    "unit_price": float(l.get("unitPrice", {}).get("value", 0)),
                    "total": float(l.get("totalAmount", {}).get("value", 0)),
                    "color": l.get("metadata", {}).get("color", "-"),
                }
                for l in mollie_data["lines"]
            ]

    except Exception as e:
        print("❌ Erreur lors de la récupération depuis Mollie :", e)

    flash("Commande payée ✅ Merci pour votre achat !", "success")
    session["cart_items"] = []

    # Pas d’email à afficher, juste la phrase générique
    order["billing_email"] = None

    return render_template("success.html", order=order)


