"""
mollie.py — Gestion des paiements Mollie + TWINT personnel temporaire
"""

from flask import Blueprint, current_app, session, request, url_for, redirect, render_template, flash
from datetime import datetime
import uuid
import os
import csv
import io
import json
import requests
import shutil
import re

from utils.tts_utils import extract_custom_text

from models.db_database import db, User, BillingInfo, Order, OrderPhoto, Stock
from services.manage_sendgrid import envoyer_email_sendgrid_Client, envoyer_email_sendgrid_Admin
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

# ============================================================
# 🗺️ Vérification du canton à partir du code postal (NPA)
# ============================================================

POSTAL_CANTONS = {}


def load_postal_cantons():
    """
    Charge la liste officielle des codes postaux suisses depuis postal_codes.csv.
    Le fichier doit contenir au minimum les colonnes : PLZ et Kantonskürzel.
    Exemples de structure :
        Ortschaftsname;PLZ;Kantonskürzel;...
    """
    global POSTAL_CANTONS

    # Détermine le chemin absolu du fichier CSV
    base_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(base_dir, "..", "static", "data", "postal_codes.csv")

    if not os.path.exists(csv_path):
        print(f"⚠️ Fichier postal_codes.csv introuvable à : {csv_path}")
        return

    try:
        with open(csv_path, newline='', encoding='utf-8') as csvfile:
            # Lecture flexible (tabulation, point-virgule ou virgule)
            sample = csvfile.read(2048)
            csvfile.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=";\t,")
            reader = csv.DictReader(csvfile, dialect=dialect)

            for row in reader:
                code = str(row.get("PLZ") or "").strip()
                canton = str(row.get("Kantonskürzel") or "").strip().upper()
                if code and canton:
                    POSTAL_CANTONS[code] = canton

        print(f"✅ {len(POSTAL_CANTONS)} codes postaux chargés depuis postal_codes.csv")

    except Exception as e:
        print(f"❌ Erreur lors du chargement des codes postaux : {e}")


def is_allowed_region(postal_code):
    """
    Vérifie si le code postal appartient à un canton autorisé (VD ou FR).
    Retourne un tuple : (bool, canton)
    """
    canton = POSTAL_CANTONS.get(str(postal_code))
    return canton in ["VD", "FR"], canton


def get_canton(postal_code):
    """Retourne simplement le canton associé au code postal (ex: 'VD', 'FR', 'ZH', etc.)"""
    return POSTAL_CANTONS.get(str(postal_code))


# Charger la base au démarrage du module
load_postal_cantons()


@mollie_bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    """Crée une commande Mollie (ordre) et redirige vers l'URL de checkout."""
    import zlib, base64  # ✅ Ajout compression
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

    # ----------------------- Compress metadata, car on doit envoyé <1ko à Mollie ------------------------
    import json, zlib, bz2, lzma, base64

    def clean_metadata(d):
        if isinstance(d, dict):
            return {k: clean_metadata(v) for k, v in d.items() if v not in (None, "", {}, [], False)}
        if isinstance(d, list):
            return [clean_metadata(v) for v in d if v not in (None, "", {}, [], False)]
        return d

    # 1) Métadonnées complètes
    metadata_full = {
        "order_id": order_uuid,
        "items": items,
        "subtotal": subtotal,
        "shipping": shipping_fee,
        "total": total,
        "currency": "CHF",
        "guest_id": session.get("guest_id"),
        "user_id": session.get("user_id"),
    }

    # 2) Nettoyage (supprime None/vides) + JSON compact
    metadata_clean = clean_metadata(metadata_full)
    metadata_json  = json.dumps(metadata_clean, ensure_ascii=False, separators=(',', ':')).encode("utf-8")

    # 3) Multi-algorithmes → on prend le plus petit
    candidates = []

    # zlib niveau 9
    try:
        z_bytes = zlib.compress(metadata_json, level=9)
        candidates.append(("zlib", z_bytes))
    except Exception:
        pass

    # bzip2 niveau 9
    try:
        b_bytes = bz2.compress(metadata_json, compresslevel=9)
        candidates.append(("bz2", b_bytes))
    except Exception:
        pass

    # lzma (xz) preset 9 — souvent le plus petit sur >1–2 Ko
    try:
        l_bytes = lzma.compress(metadata_json, preset=9)
        candidates.append(("lzma", l_bytes))
    except Exception:
        pass

    # 4) Garde le plus petit et encode en base85
    alg, best_bytes = min(candidates, key=lambda t: len(t[1])) if candidates else ("raw", metadata_json)
    metadata_encoded = base64.b85encode(best_bytes).decode("ascii")

    # 5) Logs utiles
    orig_kb = len(metadata_json) / 1024
    comp_kb = len(metadata_encoded.encode("ascii")) / 1024
    ratio   = (1 - comp_kb / orig_kb) * 100 if orig_kb else 0
    print(f"🧮 Avant: {orig_kb:.2f} Ko → Après: {comp_kb:.2f} Ko (alg={alg}, gain {ratio:.1f} %)")

    # 🚨 Garde-fou : vérifie la taille du metadata compressé
    metadata_size_bytes = len(metadata_encoded.encode("ascii"))
    MAX_MOLLIE_METADATA = 990  # 1 Ko de marge (Mollie refuse >1024 octets)

    if metadata_size_bytes > MAX_MOLLIE_METADATA:
        flash((
            f"⚠️ Votre commande est trop volumineuse ({metadata_size_bytes/1024:.2f} Ko) "
            "pour être transmise à Mollie. "
            "Merci de réduire le nombre de gobelets ou le contenu personnalisé avant de réessayer."
        ), "error")
        current_app.logger.warning(
            f"❌ Commande {order_uuid} annulée : metadata compressé = {metadata_size_bytes} octets (> {MAX_MOLLIE_METADATA})"
        )
        return redirect(url_for("cart_view"))
        
    # ✅ Metadata combiné : ID visible + bloc compressé
    order_metadata_final = {
        "order_id": order_uuid,          # 🔑 visible sans décompression
        "compressed": True,
        "encoding": "base85",
        "alg": alg,
        "data": metadata_encoded,
    }

    order_payload = {
        "amount": {"currency": "CHF", "value": f"{total:.2f}"},
        "orderNumber": order_uuid,
        "redirectUrl": url_for("mollie.payment_success", _external=True) + f"?o={order_uuid}",
        "webhookUrl": WEBHOOK_URL,
        "metadata": order_metadata_final,  # 🧠 ici !
        "locale": "fr_CH",
        "billingAddress": billing_data_mollie,
        "shippingAddress": billing_data_mollie,
        "lines": [],
    }

    # -----------------------------------------------------------------------------------------------------------


    # ➕ Lignes produits
    for it in items:
        qty = int(it.get("qty", 1))
        options = it.get("options", {}) or {}

        for i in range(qty):
            try:
                metadata_serialized = json.dumps(options, ensure_ascii=False)
            except Exception:
                metadata_serialized = str(options)

            line = {
                "type": "physical",
                "sku": it.get("sku"),
                "name": it.get("name"),
                "quantity": 1,
                "unitPrice": {"currency": "CHF", "value": f"{it.get('unit_price'):.2f}"},
                "totalAmount": {"currency": "CHF", "value": f"{it.get('unit_price'):.2f}"},
                "vatRate": "0.00",
                "vatAmount": {"currency": "CHF", "value": "0.00"},
                "metadata": {"options_json": metadata_serialized},
            }
            order_payload["lines"].append(line)

    # 🚚 Frais de livraison
    order_payload["lines"].append({
        "name": "Frais de livraison",
        "type": "shipping_fee",
        "quantity": 1,
        "unitPrice": {"currency": "CHF", "value": f"{shipping_fee:.2f}"},
        "totalAmount": {"currency": "CHF", "value": f"{shipping_fee:.2f}"},
        "vatRate": "0.00",
        "vatAmount": {"currency": "CHF", "value": "0.00"},
    })

    # 💾 Création en base avant Mollie
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

    # 🔗 Création réelle via API Mollie
    try:
        print("📦 Payload envoyé à Mollie:", json.dumps(order_payload, indent=2))
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

        # ✅ Vérification du canton (VD ou FR uniquement)
        is_valid, canton = is_allowed_region(billing_data["billing_postal"])
        if not is_valid:
            flash("🚫 Livraison uniquement possible dans les cantons de Vaud et Fribourg. D'autres cantons seront ajoutés prochainement...", "error")
            return redirect(url_for("mollie.checkout_info"))
        

        # Met à jour automatiquement le champ canton si trouvé
        if canton:
            billing_data["billing_canton"] = canton
            print(f"📍 Code postal {billing_data['billing_postal']} → canton détecté : {canton}")



        # Vérifie que l'adresse contient au moins une lettre et un chiffre
        address = billing_data["billing_address"]
        if not re.search(r"[A-Za-zÀ-ÿ]", address) or not re.search(r"\d", address):
            flash("L'adresse doit contenir à la fois du texte et un numéro de bâtiment (ex. Rue du Lac 12).", "error")
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
        import json, zlib, bz2, lzma, base64

        if metadata.get("compressed") and metadata.get("data"):
            try:
                enc = metadata.get("encoding", "base64")
                alg = metadata.get("alg", "zlib")
                raw = base64.b85decode(metadata["data"]) if enc == "base85" else base64.b64decode(metadata["data"])

                if alg == "zlib":
                    payload = zlib.decompress(raw)
                elif alg == "bz2":
                    payload = bz2.decompress(raw)
                elif alg == "lzma":
                    payload = lzma.decompress(raw)
                elif alg == "raw":
                    payload = raw
                else:
                    raise ValueError(f"Algorithme non supporté: {alg}")

                metadata = json.loads(payload.decode("utf-8"))
                current_app.logger.info(f"✅ Metadata décompressé (alg={alg}, enc={enc}, taille_json={len(payload)} octets)")
            except Exception as e:
                current_app.logger.warning(f"⚠️ Erreur décompression metadata : {e}")



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
        # 🧮 Reconstruction des items et des metadata
        # ----------------------------------------------------
        root_metadata = order_data.get("metadata", {}) or {}
        billing = order_data.get("billingAddress", {}) or {}
        items = []

        for line in order_data.get("lines", []):
            # Ignore la ligne "frais de livraison"
            if line.get("type") == "shipping_fee":
                continue

            meta = line.get("metadata", {}) or {}
            options = {}

            # 🧠 Si options_json est présent, on tente de décoder
            if "options_json" in meta:
                try:
                    options = json.loads(meta["options_json"])
                except Exception:
                    options = {"raw_metadata": str(meta)}

            # 🧾 Recompose l’article complet
            item = {
                "name": line.get("name"),
                "qty": line.get("quantity"),
                "unit_price": float(line.get("unitPrice", {}).get("value", 0)),
                "total": float(line.get("totalAmount", {}).get("value", 0)),
                "options": options,
            }
            items.append(item)

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

        # ✅ Structure finale de la commande
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
            "options": {},
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
        order.details_json = json.dumps(order_view, ensure_ascii=False)
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
                destinataire="contact@cupmyrun.ch",
                txt_path=txt_path
            )


    except requests.exceptions.RequestException as e:
        current_app.logger.exception("Erreur Mollie webhook")
        return f"Erreur Mollie: {e}", 500
    except Exception as e:
        current_app.logger.exception("Erreur inattendue webhook")
        return f"Erreur interne: {e}", 500

    return "OK", 200

@mollie_bp.route("/api/order-status/<order_id>")
def api_order_status(order_id):
    order = Order.query.filter_by(order_number=order_id).first()
    if not order:
        return {"status": "not_found"}, 404
    return {"status": order.status or "pending"}, 200

# ----------------- Route : Success -----------------
@mollie_bp.route("/payment/success")
def payment_success():
    from flask import make_response, request

    order_number = request.args.get("o", "").strip()
    if not order_number:
        flash("Commande introuvable.", "error")
        return redirect(url_for("cart_view"))

    order = Order.query.filter_by(order_number=order_number).first()
    if not order:
        flash("Commande introuvable.", "error")
        return redirect(url_for("cart_view"))

    # 🔒 Anti-cache pour tuer le back navigateur
    def nocache(resp):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    # ✅ Succès SEULEMENT si la BDD dit 'paid'
    if (order.status or "").lower() == "paid":
        details = {}
        if order.details_json:
            try:
                details = json.loads(order.details_json)
            except Exception:
                pass

        # Fusionne dans le contexte
        return render_template("success.html", order=order, details=details)
        html = render_template("success.html", order=order)
        return nocache(make_response(html))

    # 🕗 Sinon on affiche "en attente" (et on poll)
    html = render_template("pending.html", order=order)
    return nocache(make_response(html))



