import os
import pickle
import google_auth_oauthlib.flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from config import Config



# Authentification OAuth
def authenticate_google_drive():
    creds_file = Config.GOOGLE_CLIENT_SECRET_FILE
    scopes = Config.GOOGLE_DRIVE_SCOPES

    if os.path.exists('token.pickle'):  # Le jeton d'accès est stocké localement
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    # Si les credentials sont invalides ou inexistants, authentifier à nouveau
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                creds_file, scopes)
            flow.redirect_uri = 'http://localhost:5000/'

            # Ajoutez ici le login_hint pour forcer l'utilisation d'un compte spécifique
            flow.authorization_url(prompt='select_account', login_hint='stravacup@gmail.com')

            creds = flow.run_local_server(port=5000)
        
        # Sauvegarder le jeton d'accès pour la prochaine exécution
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    return creds




def get_or_create_folder(service, folder_name, parent_id=None):
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    items = results.get('files', [])

    if items:
        return items[0]['id']

    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_id:
        file_metadata['parents'] = [parent_id]

    folder = service.files().create(body=file_metadata, fields='id').execute()
    return folder.get('id')


def upload_to_google_drive_cmdFile(txt_path, image_path, internal_order_id):
    creds = authenticate_google_drive()
    service = build('drive', 'v3', credentials=creds)

    # 📁 Créer ou récupérer le dossier "Commandes"
    commandes_folder_id = get_or_create_folder(service, "Commandes")

    # 📁 Créer un sous-dossier pour la commande
    commande_folder_id = get_or_create_folder(service, internal_order_id, parent_id=commandes_folder_id)

    # 📄 Upload du fichier .txt
    if os.path.exists(txt_path):
        file_metadata = {'name': os.path.basename(txt_path), 'parents': [commande_folder_id]}
        media = MediaFileUpload(txt_path, resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        print(f"✅ Fichier .txt uploadé dans {internal_order_id}")

    # 🖼️ Upload de l'image si présente
    if image_path and os.path.exists(image_path):
        file_metadata = {'name': os.path.basename(image_path), 'parents': [commande_folder_id]}
        media = MediaFileUpload(image_path, resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        print(f"🖼️ Image uploadée dans {internal_order_id}")

