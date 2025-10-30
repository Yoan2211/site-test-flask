# tasks/refresh_strava_token.py
import os
from services.strava_service import get_valid_service_token

def refresh_strava_token_job():
    """Rafraîchit le token Strava de service (si nécessaire)."""
    print("🕓 Tâche CRON : Vérification du token Strava...")
    token = get_valid_service_token()

    if token:
        print("✅ Token Strava valide ou mis à jour.")
    else:
        print("⚠️ Échec du rafraîchissement du token Strava.")
