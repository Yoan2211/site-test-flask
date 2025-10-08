import os
from dotenv import load_dotenv, dotenv_values

# ==========================================================
# 🔹 Chargement du fichier .env AVANT toute lecture des variables
# ==========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 🔹 Chargement du .env uniquement en LOCAL

# Si l'app n'est PAS sur Render, on lit le .env local
if "RENDER" not in os.environ:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        env_values = dotenv_values(env_path)
        os.environ.update(env_values)  # on remplace d'éventuelles vieilles valeurs
        print(f"💻 Environnement local → .env chargé depuis {env_path}")
    else:
        print("⚠️ Aucun fichier .env trouvé en local.")
else:
    print("☁️ Environnement Render → on utilise les variables Render.")

print("✅ .env chargé depuis :", env_path)
print("➡️ STRAVA_CLIENT_ID =", os.getenv("STRAVA_CLIENT_ID"))
print("➡️ STRAVA_REDIRECT_URI =", os.getenv("STRAVA_REDIRECT_URI"))

# ==========================================================
# 🔹 Répertoires locaux
# ==========================================================
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_FOLDER = os.path.join(STATIC_DIR, "uploads")

# ==========================================================
# 🔹 URLs publiques
# ==========================================================
# On charge PUBLIC_BASE_URL avant le reste pour pouvoir s’en servir plus bas

# 🔹 Auto-détection : Render ou local
if "RENDER" in os.environ:
    # 👉 Environnement Render
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://" + os.getenv("RENDER_EXTERNAL_HOSTNAME", "tonapp.onrender.com"))
else:
    # 👉 Environnement local (Ngrok)
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://67dcf6d5baef.ngrok-free.app")


# ==========================================================
# 🔹 Configuration principale Flask
# ==========================================================
class Config:
    # Sécurité
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

    # Base de données

    # en local → SQLite
    DB_URL = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{os.path.join(BASE_DIR, 'database.db')}"
    )
    # Sur Render → PostgreSQL (db_RunCup)
    # Forcer le driver psycopg3 quelle que soit la forme de l’URL
    if DB_URL.startswith("postgres://"):
        DB_URL = DB_URL.replace("postgres://", "postgresql+psycopg://", 1)
    elif DB_URL.startswith("postgresql://") and "+psycopg" not in DB_URL:
        DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg://", 1)


    SQLALCHEMY_DATABASE_URI = DB_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Mollie
    MOLLIE_API_KEY = os.getenv("MOLLIE_API_KEY")
    MOLLIE_SECRET_KEY = os.getenv("MOLLIE_SECRET_KEY")

    # URLs publiques
    PUBLIC_BASE_URL = PUBLIC_BASE_URL
    WEBHOOK_URL = os.getenv("WEBHOOK_URL") or f"{PUBLIC_BASE_URL}/webhook"

    # Strava OAuth
    STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
    STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
    STRAVA_REDIRECT_URI = os.getenv("STRAVA_REDIRECT_URI")



    # SendGrid
    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")

    # Google Drive
    GOOGLE_CLIENT_SECRET_FILE = os.getenv("GOOGLE_CLIENT_SECRET_FILE")
    GOOGLE_DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive.file']

    # Sessions
    SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))
    ALLOWED_IP = os.getenv("ADMIN_ALLOWED_IP")  # récupère ton IP du .env

    # Uploads
    UPLOAD_FOLDER = UPLOAD_FOLDER
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB
    ALLOWED_ROUTE_EXT = {".png", ".jpg", ".jpeg"}

    # Prix
    PRICES = {
        "BASE": float(os.getenv("PRICE_BASE", 12.9)),
        "RESULTS": float(os.getenv("PRICE_RESULTS", 5.0)),
        "ROUTE": float(os.getenv("PRICE_ROUTE", 7.0)),
    }

    # ==========================================================
    # 🔹 Log de vérification au démarrage
    # ==========================================================
    print("=== CONFIGURATION FLASK ===")
    print("🌍 PUBLIC_BASE_URL :", PUBLIC_BASE_URL)
    print("🔗 STRAVA_REDIRECT_URI :", STRAVA_REDIRECT_URI)
    print("💳 WEBHOOK_URL :", WEBHOOK_URL)
    print("============================")


# ==========================================================
# 🔹 Environnements
# ==========================================================
class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
