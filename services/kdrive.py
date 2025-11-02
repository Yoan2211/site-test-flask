#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Création d’un sous-dossier pour une commande et upload des fichiers .txt et .stl
dans 'Commands paid to print' sur kDrive Infomaniak.
"""

import io
import requests
from config import Config



access_token = Config.INFOMANIAK_ACCESS_TOKEN
drive_id = Config.INFOMANIAK_DRIVE_ID


# ==========================================================
# 📁 Recherche d’un dossier par nom
# ==========================================================
def get_directory_id_by_name(folder_name: str) -> int:
    """Recherche l’ID d’un dossier kDrive par son nom exact."""
    url = f"https://api.infomaniak.com/3/drive/{drive_id}/files/search"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"search": folder_name, "with": "parents"}

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"❌ HTTP {response.status_code} : {response.text}")

    data = response.json()
    if data.get("result") != "success":
        raise RuntimeError(f"⚠️ Erreur API : {data}")

    results = data.get("data", [])
    print(f"🔎 {len(results)} élément(s) trouvé(s) contenant '{folder_name}'")

    for item in results:
        print(f"➡️ {item.get('name')}  |  type={item.get('type')}  |  id={item.get('id')}")
        if item.get("name") == folder_name and item.get("type") in ("dir", "directory"):
            print(f"📂 Dossier trouvé : '{item['name']}' (ID={item['id']})")
            return item["id"]

    raise FileNotFoundError(f"❌ Dossier '{folder_name}' introuvable sur le Drive {drive_id}.")


# ==========================================================
# 🆕 Création d’un sous-dossier
# ==========================================================
def create_subdirectory(parent_id: int, new_folder_name: str, color: str = "#0098ff") -> int:
    """Crée un sous-dossier dans un dossier parent (API officielle)."""
    url = f"https://api.infomaniak.com/3/drive/{drive_id}/files/{parent_id}/directory"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {"name": new_folder_name, "color": color}

    print(f"📁 Création du dossier '{new_folder_name}' dans le dossier #{parent_id}...")
    response = requests.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        raise RuntimeError(f"❌ HTTP {response.status_code} : {response.text}")

    res = response.json()
    if res.get("result") == "success":
        data = res["data"]
        print(f"✅ Dossier créé : '{data['name']}' (ID={data['id']})")
        return data["id"]
    else:
        raise RuntimeError(f"⚠️ Erreur API : {res}")


# ==========================================================
# 🔼 Upload d’un fichier en mémoire
# ==========================================================
def upload_to_kdrive(directory_id: int, file_bytes: bytes, file_name: str) -> dict:
    """Upload direct d’un fichier binaire (depuis mémoire) vers un dossier kDrive."""
    if not file_bytes or not file_name:
        raise ValueError("Les paramètres 'file_bytes' et 'file_name' sont obligatoires.")

    file_size = len(file_bytes)
    url = (
        f"https://api.infomaniak.com/3/drive/{drive_id}/upload"
        f"?total_size={file_size}"
        f"&file_name={file_name}"
        f"&directory_id={directory_id}"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream"
    }

    print(f"📤 Envoi de '{file_name}' ({file_size} octets) vers dossier #{directory_id}...")
    response = requests.post(url, headers=headers, data=io.BytesIO(file_bytes))

    if response.status_code == 200:
        result = response.json()
        if result.get("result") == "success":
            data = result["data"]
            print(f"✅ Fichier uploadé : {data['name']} (ID={data['id']})")
            return data
        else:
            raise RuntimeError(f"⚠️ Erreur API : {result}")
    else:
        raise RuntimeError(f"❌ HTTP {response.status_code} : {response.text}")


# ==========================================================
# 🚀 Fonction principale : upload d'une commande dans un sous-dossier
# ==========================================================
def upload_order_to_kdrive(order_number: str, txt_bytes: bytes, stl_bytes: bytes | None = None):
    """
    Crée un sous-dossier pour la commande dans 'Commands paid to print'
    et upload les fichiers .txt et .stl à l’intérieur (si disponibles).
    """
    ROOT_FOLDER = "Commands paid to print"
    print(f"\n🚀 Upload de la commande #{order_number}")

    # 1️⃣ Créer un sous-dossier pour la commande
    order_folder = f"CMD_{order_number}"
    sub_id = create_subdirectory(12, order_folder)

    # 2️⃣ Upload du fichier TXT (obligatoire)
    try:
        upload_to_kdrive(sub_id, txt_bytes, f"commande_{order_number}.txt")
        print(f"✅ Fichier TXT uploadé pour la commande {order_number}")
    except Exception as e:
        print(f"❌ Erreur lors de l’upload du TXT : {e}")
        return  # on arrête ici, car le TXT est indispensable

    # 3️⃣ Upload du STL (facultatif)
    if stl_bytes and isinstance(stl_bytes, (bytes, bytearray)) and len(stl_bytes) > 0:
        try:
            upload_to_kdrive(sub_id, stl_bytes, f"commande_{order_number}.stl")
            print(f"✅ Fichier STL uploadé pour la commande {order_number}")
        except Exception as e:
            print(f"❌ Erreur lors de l’upload du STL : {e}")
    else:
        print(f"⚠️ Aucun fichier STL à uploader pour la commande {order_number}")

    print(f"🎉 Commande #{order_number} transférée avec succès dans '{order_folder}' !\n")



# ==========================================================
# 🧪 TEST LOCAL
# ==========================================================
if __name__ == "__main__":
    import os

    try:
        # Exemple de commande fictive
        order_number = "1050"

        # Contenu du fichier texte (en mémoire)
        txt_content = (
            f"Commande #{order_number}\n"
            "Client : Jean Dupont\n"
            "Produit : Gobelet Strava 33cl\n"
            "Couleur : Orange / Noir / Blanc\n"
        ).encode("utf-8")

        # Lecture du STL local
        stl_path = r"C:\Users\yoana\Downloads\track_16090365038.stl"
        if not os.path.exists(stl_path):
            raise FileNotFoundError(f"⚠️ Fichier STL introuvable : {stl_path}")
        with open(stl_path, "rb") as f:
            stl_bytes = f.read()

        # Lancer le transfert complet
        upload_order_to_kdrive(order_number, txt_content, stl_bytes)

    except Exception as e:
        print(f"\n❌ Erreur pendant le test : {e}")
