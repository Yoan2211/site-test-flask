import os
import base64
from flask import render_template
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
from config import Config

SENDGRID_API_KEY= Config.SENDGRID_API_KEY

def envoyer_email_sendgrid_Client(order, destinataire, image_path=None):
    subject = f"Confirmation de commande #{order['id']}"
    html_content = render_template("email.html", order=order)

    message = Mail(
        from_email="stravacup@gmail.com",
        to_emails=destinataire,
        subject=subject,
        html_content=html_content
    )

    # Optionnel : image jointe
    if image_path and os.path.exists(image_path):
        with open(image_path, 'rb') as f:
            data = f.read()
            encoded = base64.b64encode(data).decode()
            attachment = Attachment(
                FileContent(encoded),
                FileName(os.path.basename(image_path)),
                FileType("image/png"),
                Disposition("attachment")
            )
            message.attachment = attachment

    sg = SendGridAPIClient(SENDGRID_API_KEY)
    
    response = sg.send(message)
    print(f"📧 Email Client envoyé ({destinataire}) — Statut: {response.status_code}")

def envoyer_email_sendgrid_Admin(order, destinataire, txt_path):
    """
    Envoie un email à l'admin contenant :
      - le fichier .txt de la commande
      - le fichier .stl du tracé Strava (si présent)
    Aucun visuel/image n'est attaché.
    """
    subject = f"Nouvelle commande #{order['id']} (données complètes)"
    html_content = render_template("email_admin.html", order=order)

    message = Mail(
        from_email="stravacup@gmail.com",
        to_emails=destinataire,
        subject=subject,
        html_content=html_content
    )

    # 🔹 1. pièce jointe .txt (détails de la commande)
    if txt_path and os.path.exists(txt_path):
        with open(txt_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
            attachment = Attachment(
                FileContent(encoded),
                FileName(os.path.basename(txt_path)),
                FileType("text/plain"),
                Disposition("attachment")
            )
            message.add_attachment(attachment)
            print(f"📎 Fichier TXT ajouté : {txt_path}")

    # 🔹 2. pièce jointe STL (tracé Strava)
    stl_path = order.get("options", {}).get("stl_path")
    if stl_path and os.path.exists(stl_path):
        with open(stl_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
            attachment = Attachment(
                FileContent(encoded),
                FileName(os.path.basename(stl_path)),
                FileType("application/sla"),
                Disposition("attachment")
            )
            message.add_attachment(attachment)
            print(f"📎 Fichier STL ajouté : {stl_path}")
    else:
        print("⚠️ Aucun fichier STL à attacher à l'email admin.")

    # 🔹 3. envoi
    sg = SendGridAPIClient(SENDGRID_API_KEY)
    response = sg.send(message)
    print(f"📧 Email Admin envoyé ({destinataire}) — Statut: {response.status_code}")


def ajouter_piece_jointe_stl(message, stl_path):
    """
    Attache un fichier STL à un email SendGrid s'il existe.
    """
    if not stl_path or not os.path.exists(stl_path):
        print(f"⚠️ Aucun fichier STL trouvé à attacher : {stl_path}")
        return
    with open(stl_path, "rb") as f:
        data = f.read()
        encoded = base64.b64encode(data).decode()
        attachment = Attachment(
            FileContent(encoded),
            FileName(os.path.basename(stl_path)),
            FileType("application/sla"),
            Disposition("attachment")
        )
        message.add_attachment(attachment)
        print(f"📎 Pièce jointe STL ajoutée : {stl_path}")


