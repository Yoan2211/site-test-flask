# fix_encoding.py
import os

def convert_to_utf8(filepath):
    try:
        # Essaye de lire en UTF-8
        with open(filepath, "r", encoding="utf-8") as f:
            f.read()
        print(f"✅ Déjà UTF-8 : {filepath}")
    except UnicodeDecodeError:
        # Sinon, tente latin-1
        with open(filepath, "r", encoding="latin-1") as f:
            content = f.read()
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        print(f"🔄 Converti en UTF-8 : {filepath}")

def scan_and_fix_templates(folder="templates"):
    if not os.path.exists(folder):
        print(f"❌ Le dossier {folder} n’existe pas")
        return
    for root, _, files in os.walk(folder):
        for file in files:
            if file.endswith(".html"):
                convert_to_utf8(os.path.join(root, file))

if __name__ == "__main__":
    scan_and_fix_templates("templates")
