import os
from flask import Flask
from config import DevelopmentConfig, ProductionConfig
from models.db_database import db
from dotenv import load_dotenv


def create_app():
    """
    Crée et configure l’application Flask.
    Appelée depuis app.py : 
        from __init__ import create_app
        app = create_app()
    """

    # 1️⃣ Charger le fichier .env en priorité
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(BASE_DIR, ".env"))

    # 2️⃣ Choisir la config selon l’environnement
    env = os.getenv("FLASK_ENV", "development").lower()
    if env == "production":
        config_object = ProductionConfig
        print("🚀 Mode PRODUCTION activé")
    else:
        config_object = DevelopmentConfig
        print("🧩 Mode DÉVELOPPEMENT activé")

    # 3️⃣ Créer l'application Flask
    app = Flask(__name__)
    app.config.from_object(config_object)

    # 4️⃣ Initialiser les extensions (base de données)
    db.init_app(app)

    # 5️⃣ Importer et enregistrer les blueprints
    # ⚠️ Ces imports DOIVENT être ici (pas en haut du fichier)
    from routes.strava_routes import strava_bp
    from routes.auth import auth_bp

    app.register_blueprint(strava_bp, url_prefix="/strava")
    app.register_blueprint(auth_bp)

    return app
