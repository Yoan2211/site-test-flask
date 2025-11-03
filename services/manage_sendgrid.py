import os
import base64
from flask import render_template
from services.strava_service import StravaService
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
from config import Config
from utils.tts_utils import extract_custom_text

SENDGRID_API_KEY= Config.SENDGRID_API_KEY
import os
import base64
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition
)
from flask import render_template
from dotenv import load_dotenv

def envoyer_email_sendgrid_Client(order, destinataire, image_path=None):
    from types import SimpleNamespace
    import base64, os
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
    from flask import render_template

    # ✅ Convertit le dictionnaire en objet pour accès .shipping
    def to_obj(d):
        if isinstance(d, dict):
            return SimpleNamespace(**{k: to_obj(v) for k, v in d.items()})
        elif isinstance(d, list):
            return [to_obj(x) for x in d]
        return d

    order_obj = to_obj(order)

    subject = f"Confirmation de commande #{order_obj.id}"
    html_content = render_template("email_client.html", order=order_obj)

    message = Mail(
        from_email="contact@cupmyrun.ch",
        to_emails=destinataire,
        subject=subject,
        html_content=html_content
    )

    # Optionnel : joindre une image
    if image_path and os.path.exists(image_path):
        with open(image_path, 'rb') as f:
            encoded = base64.b64encode(f.read()).decode()
            attachment = Attachment(
                FileContent(encoded),
                FileName(os.path.basename(image_path)),
                FileType("image/png"),
                Disposition("attachment")
            )
            message.attachment = attachment

    sg = SendGridAPIClient(os.getenv("SENDGRID_API_KEY"))
    response = sg.send(message)
    print(f"📧 Email Client envoyé ({destinataire}) — Statut: {response.status_code}")


def envoyer_email_sendgrid_Admin(order, destinataire, txt_path=None):
    """
    Envoie un email à l'administrateur contenant :
      - le fichier .txt de la commande
      - un STL texte par texte personnalisé (si saisi)
      - un STL Strava par activité liée (si présente)
    """

    import base64, hashlib, os, re
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (
        Mail, Attachment, FileContent, FileName, FileType, Disposition
    )
    from flask import render_template
    from dotenv import load_dotenv
    from types import SimpleNamespace
    from app import generate_stl_from_activity
    from services.stl_manager import generate_txt2stl
    from services.strava_service import StravaService
    from utils.tts_utils import extract_custom_text

    # ============================================================
    # ⚙️ Config & utilitaires
    # ============================================================
    load_dotenv()
    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
    if not SENDGRID_API_KEY:
        raise RuntimeError("SENDGRID_API_KEY manquante dans le .env")

    def to_obj(d):
        if isinstance(d, dict):
            return SimpleNamespace(**{k: to_obj(v) for k, v in d.items()})
        elif isinstance(d, list):
            return [to_obj(x) for x in d]
        return d

    def safe_filename(name: str):
        """Crée un nom de fichier sûr (sans caractères spéciaux)."""
        return re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())[:50] or "file"

    # ============================================================
    # 🧾 Préparation des données commande
    # ============================================================
    subtotal = order.get("subtotal", 0.0)
    shipping = order.get("shipping", 0.0)
    total = order.get("total", subtotal + shipping)

    order_data = {
        "id": order.get("id"),
        "currency": order.get("currency", "CHF"),
        "subtotal": subtotal,
        "shipping": shipping,
        "total": total,
        "billingAddress": order.get("billingAddress", {}),
        "line_items": order.get("line_items", []),
    }

    order_obj = to_obj(order_data)

    # ============================================================
    # ✉️ Préparation du contenu du mail
    # ============================================================
    subject = f"Nouvelle commande #{order_obj.id} — Détails complets"
    html_content = render_template("email_admin.html", order=order_obj)
    message = Mail(
        from_email="contact@cupmyrun.ch",
        to_emails=destinataire,
        subject=subject,
        html_content=html_content,
    )

    # ============================================================
    # 📎 1️⃣ Ajout du fichier .TXT de la commande
    # ============================================================
    if txt_path and os.path.exists(txt_path):
        with open(txt_path, "rb") as f:
            txt_data = f.read()
        txt_attachment = Attachment(
            FileContent(base64.b64encode(txt_data).decode()),
            FileName(os.path.basename(txt_path)),
            FileType("text/plain"),
            Disposition("attachment"),
        )
        message.add_attachment(txt_attachment)
        print(f"📎 Fichier TXT joint : {os.path.basename(txt_path)} ✅")

    # ============================================================
    # 🧩 2️⃣ Génération des STL (texte + Strava) pour chaque gobelet
    # ============================================================
    strava_done = set()
    text_done = set()

    for i, item in enumerate(order.get("line_items", []), start=1):
        opts = item.get("options", {}) or {}

        # --- Texte personnalisé ---
        custom_text = opts.get("custom_text") or ""
        if opts.get("add_text") and custom_text.strip():
            if custom_text not in text_done:
                try:
                    stl_bytes_text = generate_txt2stl(custom_text)
                    filename = safe_filename(f"text_{i}_{custom_text}.stl")
                    text_done.add(custom_text)

                    text_attachment = Attachment(
                        FileContent(base64.b64encode(stl_bytes_text).decode()),
                        FileName(filename),
                        FileType("application/sla"),
                        Disposition("attachment"),
                    )
                    message.add_attachment(text_attachment)
                    print(f"📎 STL texte joint pour item {i} : {filename}")
                except Exception as e:
                    print(f"❌ Erreur STL texte (item {i}): {e}")

        # --- Tracé Strava ---
        activity_id = opts.get("activity_id")
        if opts.get("add_route") and activity_id:
            if activity_id not in strava_done:
                try:
                    user, token = StravaService.get_token_for_order(order)
                    stl_bytes_strava = generate_stl_from_activity(activity_id, token=token)
                    filename = f"track_{activity_id}.stl"
                    strava_done.add(activity_id)

                    stl_attachment = Attachment(
                        FileContent(base64.b64encode(stl_bytes_strava).decode()),
                        FileName(filename),
                        FileType("application/sla"),
                        Disposition("attachment"),
                    )
                    message.add_attachment(stl_attachment)
                    print(f"📎 STL Strava joint : {filename}")
                except Exception as e:
                    print(f"❌ Erreur STL Strava (activité {activity_id}): {e}")

    # ============================================================
    # 🚀 3️⃣ Envoi de l’email
    # ============================================================
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"📧 Email Admin envoyé à {destinataire} — Statut {response.status_code}")
    except Exception as e:
        print(f"❌ Erreur envoi SendGrid : {e}")
        raise

    return {"nb_texts": len(text_done), "nb_tracks": len(strava_done)}




def generate_and_attach_stl_text(custom_text: str, message, html_content: str):
    """Génère un STL à partir du texte et l’attache à un message SendGrid."""
    import base64, re, hashlib
    from test_text_to_stl import generate_txt2stl
    from sendgrid.helpers.mail import Attachment, FileContent, FileName, FileType, Disposition

    if not custom_text.strip():
        print("⚠️ Aucun texte fourni pour génération STL texte.")
        return html_content, None

    print(f"🧱 Génération STL texte pour '{custom_text[:40]}...'")
    stl_bytes = generate_txt2stl(custom_text)
    stl_hash = hashlib.sha256(stl_bytes).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", "_".join(custom_text.splitlines()))[:50] or "text"

    html_content += f"""
    <hr><p><strong>Hash STL Texte (SHA256)</strong><br>{stl_hash}</p>
    <p><em>Fichier joint : {safe_name}.stl</em></p>
    """

    attachment = Attachment(
        FileContent(base64.b64encode(stl_bytes).decode()),
        FileName(f"{safe_name}.stl"),
        FileType("application/sla"),
        Disposition("attachment"),
    )
    message.add_attachment(attachment)
    print(f"📎 STL texte joint : {safe_name}.stl ✅")

    return html_content, stl_hash



def _get_strava_token_for_order_like(order_dict):
    """
    Essaie de récupérer un token Strava utilisable hors session :
    1) token passé dans options (strava_token)
    2) via user_id (compte lié)
    3) via guest_id (session invitée)
    """
    import time
    token = (order_dict.get("options") or {}).get("strava_token")
    if token:
        return token

    user_id = order_dict.get("user_id")
    guest_id = order_dict.get("guest_id")

    # 1) via user_id (si tu as un helper dédié)
    if user_id and hasattr(StravaService, "get_token_by_user_id"):
        try:
            token = StravaService.get_token_by_user_id(user_id)
            if token:
                return token
        except Exception:
            pass

    # 2) via guest_id (comme dans le webhook)
    if guest_id:
        try:
            from models.db_database import GuestStravaSession
            entry = GuestStravaSession.query.filter_by(guest_id=guest_id).first()
            if entry and entry.strava_token and entry.strava_token_expires_at > time.time():
                return entry.strava_token
        except Exception:
            pass

    return None

def envoyer_email_contact_sendgrid(name, email, message):
    """
    Envoie un email de contact via SendGrid vers l'adresse principale du site.
    Utilisée par la route /contact (formulaire Flask).
    """
    try:
        SENDGRID_API_KEY = Config.SENDGRID_API_KEY
        DESTINATAIRE = "contact@cupmyrun.ch"

        # Remplacer les sauts de ligne avant de construire la f-string
        message_html = message.replace("\n", "<br>")

        subject = f"📬 Nouveau message de contact — {name}"
        html_content = f"""
        <h2>Nouveau message depuis le site CupMyRun</h2>
        <p><strong>Nom :</strong> {name}</p>
        <p><strong>Email :</strong> {email}</p>
        <p><strong>Message :</strong></p>
        <blockquote>{message_html}</blockquote>
        """

        message_obj = Mail(
            from_email="contact@cupmyrun.ch",
            to_emails=DESTINATAIRE,
            subject=subject,
            html_content=html_content
        )

        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message_obj)
        print(f"📧 Email de contact envoyé à {DESTINATAIRE} — Statut {response.status_code}")

        return True

    except Exception as e:
        print(f"❌ Erreur lors de l’envoi de l’email de contact : {e}")
        return False

##################################### Test fucntions #####################################
def envoyer_email_sendgrid_test(destinataire):
    """
    TEST : envoie un fichier STL situé dans static/stl/
    pour vérifier qu'il est bien reçu intact via SendGrid.
    """
    try:
        # === 1️⃣ Fichier de test ===
        stl_path = os.path.join("static", "stl", "test_model.stl")  # change le nom selon ton fichier réel
        if not os.path.exists(stl_path):
            raise FileNotFoundError(f"Fichier STL introuvable : {stl_path}")

        # === 2️⃣ Lecture + encodage base64 ===
        with open(stl_path, "rb") as f:
            stl_data = f.read()
        encoded_stl = base64.b64encode(stl_data).decode()

        # === 3️⃣ Création du message ===
        message = Mail(
            from_email="contact@cupmyrun.ch",
            to_emails=destinataire,
            subject="[TEST STL] Envoi fichier STL intact",
            html_content=f"""
                <p>Voici un test d'envoi d'un fichier STL depuis SendGrid.</p>
                <p>Fichier : <b>{os.path.basename(stl_path)}</b></p>
                <p>Taille : {len(stl_data)} octets</p>
            """,
        )

        # === 4️⃣ Ajout de la pièce jointe encodée ===
        attachment = Attachment(
            FileContent(encoded_stl),
            FileName(os.path.basename(stl_path)),
            FileType("application/sla"),
            Disposition("attachment"),
        )
        message.add_attachment(attachment)

        # === 5️⃣ Envoi via SendGrid ===
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"✅ Email envoyé : {response.status_code}")
        return f"✅ Email envoyé à {destinataire} avec {os.path.basename(stl_path)}"

    except Exception as e:
        print(f"❌ Erreur lors de l'envoi : {e}")
        return f"❌ Erreur : {e}"

def test_envoi_txt(destinataire):
    """
    Teste l'envoi d'un simple fichier TXT via SendGrid.
    """
    txt_path = os.path.join("static", "test_envoi.txt")

    # --- Création du fichier de test s'il n'existe pas ---
    if not os.path.exists(txt_path):
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("Ceci est un test d'envoi de fichier .txt via SendGrid.")
        print(f"✅ Fichier créé : {txt_path}")

    try:
        # Lecture et encodage
        with open(txt_path, "rb") as f:
            data = f.read()
        encoded_txt = base64.b64encode(data).decode()
        print(f"📏 Taille fichier : {len(data)} octets")

        # Création du message
        message = Mail(
            from_email="contact@cupmyrun.ch",
            to_emails=destinataire,
            subject="[TEST TXT] Envoi d'un fichier texte",
            html_content="<p>Test d'envoi d'un fichier .txt en pièce jointe.</p>",
        )

        # Ajout du fichier encodé
        attachment = Attachment(
            FileContent(encoded_txt),
            FileName("test_envoi.txt"),
            FileType("text/plain"),
            Disposition("attachment"),
        )
        message.add_attachment(attachment)

        # Envoi
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"📧 Statut SendGrid : {response.status_code}")
        if response.status_code in (200, 202):
            print("✅ Email de test envoyé avec succès !")
        else:
            print(f"⚠️ Envoi terminé avec statut inattendu : {response.status_code}")

    except Exception as e:
        print(f"❌ Erreur lors de l'envoi : {e}")

def envoyer_email_sendgrid_Admin_test(destinataire):
    """
    🔧 Test complet d'envoi Admin :
    - Génère un fichier TXT de test (static/test_admin.txt)
    - Simule une commande
    - Envoie l'email Admin avec pièce jointe TXT
    - (Pas de Strava ni de STL ici)
    """
    import os
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
    import base64
    from dotenv import load_dotenv

    print("🚀 Test d'envoi Admin...")

    # --- 1️⃣ Charger clé API ---
    if not SENDGRID_API_KEY:
        print("❌ Clé SendGrid manquante dans .env (SENDGRID_API_KEY)")
        return

    # --- 2️⃣ Création du fichier TXT de test ---
    txt_path = os.path.join("static", "test_admin.txt")
    os.makedirs("static", exist_ok=True)
    if not os.path.exists(txt_path):
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("Ceci est un test complet d'envoi Admin avec un fichier .txt joint.\n")
        print(f"✅ Fichier TXT créé : {txt_path}")

    # --- 3️⃣ Construction du contenu HTML ---
    html_content = """
        <h2>🧾 Test d'envoi Admin</h2>
        <p>Ce message simule l'envoi d'une commande complète à l'administrateur.</p>
        <p><strong>Fichier joint :</strong> test_admin.txt</p>
        <hr>
        <p style="color:#888;">Test interne CupMyRun — aucun STL généré.</p>
    """

    message = Mail(
        from_email="contact@cupmyrun.ch",
        to_emails=destinataire,
        subject="[TEST ADMIN] Envoi d'un fichier TXT (sans STL)",
        html_content=html_content,
    )

    # --- 4️⃣ Lecture et ajout du fichier TXT ---
    try:
        with open(txt_path, "rb") as f:
            txt_data = f.read()
        encoded_txt = base64.b64encode(txt_data).decode()

        attachment = Attachment(
            FileContent(encoded_txt),
            FileName("test_admin.txt"),
            FileType("text/plain"),
            Disposition("attachment"),
        )
        message.add_attachment(attachment)
        print(f"📎 TXT ajouté : test_admin.txt ({len(txt_data)} octets)")
    except Exception as e:
        print(f"❌ Erreur lors de la lecture du TXT : {e}")
        return

    # --- 5️⃣ Envoi via SendGrid ---
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"📧 Email Admin de test envoyé à {destinataire} — Statut {response.status_code}")
        if response.status_code in (200, 202):
            print("✅ Test Admin : succès total !")
        else:
            print(f"⚠️ Test Admin : statut inattendu {response.status_code}")
    except Exception as e:
        print(f"❌ Erreur SendGrid : {e}")


##########################################################################################


if __name__ == "__main__":
    envoyer_email_sendgrid_Admin_test("stravacup@gmail.com")



