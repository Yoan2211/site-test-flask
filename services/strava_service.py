import time
import requests
from flask import current_app
from models.db_database import db, User

class StravaService:
    @staticmethod
    def get_token(user: User) -> str | None:
        """Retourne un token valide, rafraîchit si expiré."""
        if not user.strava_access_token:
            return None

        if user.strava_token_expires_at and time.time() > user.strava_token_expires_at:
            refreshed = StravaService.refresh_token(user)
            if not refreshed:
                return None

        return user.strava_access_token

    @staticmethod
    def refresh_token(user: User) -> bool:
        """Rafraîchit le token Strava si expiré."""
        response = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": current_app.config["STRAVA_CLIENT_ID"],
            "client_secret": current_app.config["STRAVA_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": user.strava_refresh_token,
        })

        if response.status_code != 200:
            return False

        data = response.json()
        user.strava_access_token = data["access_token"]
        user.strava_refresh_token = data["refresh_token"]
        user.strava_token_expires_at = data["expires_at"]
        db.session.commit()
        return True

    @staticmethod
    def get_token_from_session() -> tuple[User | None, str | None]:
        """
        Retourne (user, token) pour Strava.
        - Si utilisateur connecté → token depuis BDD (rafraîchi si nécessaire)
        - Sinon guest → token depuis session (si non expiré)
        """
        from flask import session
        import time

        user: User | None = None
        token: str | None = None

        # 🔹 Utilisateur connecté
        if "user_id" in session:
            user = User.query.get(session["user_id"])
            if user:
                # Renvoie le token depuis la BDD, avec rafraîchissement automatique si nécessaire
                token = StravaService.get_token(user)

        # 🔹 Token guest dans session
        if not token:
            token = session.get("strava_token")
            expires_at = session.get("strava_expires_at", 0)
            # Vérifie l'expiration
            if not token or time.time() >= expires_at:
                token = None

        return user, token


    @staticmethod
    def exchange_code_for_token(code: str) -> dict | None:
        """Échange le code d'autorisation contre un token."""
        response = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": current_app.config["STRAVA_CLIENT_ID"],
            "client_secret": current_app.config["STRAVA_CLIENT_SECRET"],
            "code": code,
            "grant_type": "authorization_code",
        })

        if response.status_code != 200:
            return None

        return response.json()

    @staticmethod
    def fetch_activity(token: str, activity_id: str) -> dict | None:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}",
            headers=headers
        )
        return response.json() if response.status_code == 200 else None

    @staticmethod
    def fetch_activities(token: str, per_page=10, page=1) -> list | None:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers=headers,
            params={"per_page": per_page, "page": page}
        )
        return response.json() if response.status_code == 200 else None


