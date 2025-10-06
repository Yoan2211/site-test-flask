import os
from dotenv import load_dotenv

# ==========================================================
# 🔹 Chargement du fichier .env AVANT toute lecture des variables
# ==========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

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
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://71134bf575ca.ngrok-free.app")


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
    # sur Render → PostgreSQL (db_RunCup)
    # Render envoie souvent 'postgres://', alors que SQLAlchemy veut 'postgresql://'
    if DB_URL.startswith("postgres://"):
        DB_URL = DB_URL.replace("postgres://", "postgresql+psycopg://", 1)

    SQLALCHEMY_DATABASE_URI = DB_URL

    # Mollie
    MOLLIE_API_KEY = os.getenv("MOLLIE_API_KEY")
    MOLLIE_SECRET_KEY = os.getenv("MOLLIE_SECRET_KEY")

    # URLs publiques
    PUBLIC_BASE_URL = PUBLIC_BASE_URL
    WEBHOOK_URL = os.getenv("WEBHOOK_URL") or f"{PUBLIC_BASE_URL}/webhook"

    # Strava OAuth
    STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
    STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
    STRAVA_REDIRECT_URI = os.getenv("STRAVA_REDIRECT_URI") or f"{PUBLIC_BASE_URL}/strava/authorized"



    # SendGrid
    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")

    # Google Drive
    GOOGLE_CLIENT_SECRET_FILE = os.getenv("GOOGLE_CLIENT_SECRET_FILE")
    GOOGLE_DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive.file']

    # Sessions
    SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))

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
