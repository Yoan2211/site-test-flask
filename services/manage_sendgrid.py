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


def envoyer_email_sendgrid_Admin(order, destinataire, txt_path, image_path=None):
    subject = f"Nouvelle commande #{order['id']} (données complètes)"
    html_content = render_template("email_admin.html", order=order)

    message = Mail(
        from_email="stravacup@gmail.com",
        to_emails=destinataire,
        subject=subject,
        html_content=html_content
    )

    # Ajout du fichier .txt
    with open(txt_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()
        message.add_attachment(Attachment(
            FileContent(encoded),
            FileName(os.path.basename(txt_path)),
            FileType("text/plain"),
            Disposition("attachment")
        ))

    # Ajout de l'image si dispo
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as img:
            encoded_img = base64.b64encode(img.read()).decode()
            message.add_attachment(Attachment(
                FileContent(encoded_img),
                FileName(os.path.basename(image_path)),
                FileType("image/png"),
                Disposition("attachment")
            ))

    sg = SendGridAPIClient(SENDGRID_API_KEY)
    
    response = sg.send(message)
    print(f"📧 Email Admin envoyé ({destinataire}) — Statut: {response.status_code}")


