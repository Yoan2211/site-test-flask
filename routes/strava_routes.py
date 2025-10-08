from flask import Blueprint, redirect, request, session, flash, url_for, render_template, current_app, send_file
from models.db_database import db, User
from services.strava_service import StravaService
from utils.tts_utils import format_pace_mmss
from utils.image_utils import render_track_image
import polyline
import requests

strava_bp = Blueprint("strava", __name__)

# ----------------- Logs Strava -----------------
@strava_bp.route("/connect")
def connect():
    """Redirige vers Strava pour autorisation"""
    auth_url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={current_app.config['STRAVA_CLIENT_ID']}"
        f"&response_type=code"
        f"&redirect_uri={current_app.config['STRAVA_REDIRECT_URI']}"
        f"&approval_prompt=auto"
        f"&scope=activity:read"
    )

    return redirect(auth_url)

@strava_bp.route("/authorized")
def authorized():
    code = request.args.get("code")
    if not code:
        flash("Erreur lors de la connexion Strava.", "error")
        return redirect(url_for("gobelet"))

    token_data = StravaService.exchange_code_for_token(code)
    if not token_data or not token_data.get("access_token"):
        flash("Impossible de récupérer le token Strava.", "error")
        return redirect(url_for("gobelet"))

    # Sauvegarde temporaire de l'activité sélectionnée guest
    pending_activity = session.get("selected_activity")

    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user:
            # ✅ Toujours écraser / associer le token Strava au compte user
            user.strava_access_token = token_data["access_token"]
            user.strava_refresh_token = token_data["refresh_token"]
            user.strava_token_expires_at = token_data["expires_at"]
            db.session.commit()

            # Supprimer tous les tokens guest (fusion)
            session.pop("strava_token", None)
            session.pop("strava_refresh_token", None)
            session.pop("strava_expires_at", None)

            # Réapplique l'activité sélectionnée si elle existait
            if pending_activity:
                session["selected_activity"] = pending_activity

            flash("Compte Strava lié à votre profil ✅", "success")
    else:
        # Cas guest
        session["strava_token"] = token_data["access_token"]
        session["strava_expires_at"] = token_data["expires_at"]
        session["strava_refresh_token"] = token_data["refresh_token"]
        session.permanent = True
        flash("Strava connecté en mode invité ✅", "success")

    StravaService.increment_strava_connections()

    return redirect(url_for("strava.activities"))

@strava_bp.route("/disconnect", methods=["POST"])
def disconnect():
    """Déconnexion Strava (libère les slots d'athlètes connectés mais garde le lien pour les users)"""
    user = None
    token_cleared = False

    # 🔹 Cas utilisateur connecté
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user and user.strava_access_token:
            # 🔸 Révocation du token actif côté Strava (libère un athlète connecté)
            try:
                requests.post(
                    "https://www.strava.com/oauth/deauthorize",
                    headers={"Authorization": f"Bearer {user.strava_access_token}"},
                    timeout=5
                )
            except Exception as e:
                print(f"Erreur déconnexion Strava : {e}")

            # 🔸 On efface seulement le token actif (mais pas le refresh_token ni l'athlete_id)
            user.strava_access_token = None
            user.strava_token_expires_at = None
            db.session.commit()
            token_cleared = True

    # 🔹 Cas "guest" connecté via session seulement
    if "strava_token" in session:
        try:
            requests.post(
                "https://www.strava.com/oauth/deauthorize",
                headers={"Authorization": f"Bearer {session['strava_token']}"},
                timeout=5
            )
        except Exception as e:
            print(f"Erreur déconnexion Strava (guest) : {e}")

        # 🔸 On supprime complètement la session Strava du guest
        session.pop("strava_token", None)
        session.pop("strava_refresh_token", None)
        session.pop("strava_expires_at", None)
        session.pop("selected_activity", None)
        token_cleared = True

    # 🔹 Indicateur pour le template
    session["strava_just_disconnected"] = True

    # 🔹 Messages utilisateur
    if token_cleared:
        if user:
            flash("Strava déconnecté, lien conservé ✅", "success")
        else:
            flash("Compte Strava complètement déconnecté ✅", "success")

        StravaService.increment_strava_connections()
    else:
        flash("Aucun compte Strava à déconnecter.", "info")

    # 🔹 Redirection adaptée
    return redirect(url_for("gobelet"))



# ----------------- Activités Strava -----------------
@strava_bp.route("/activities")
def activities():
    """
    Affiche les activités Strava si le token est disponible.
    - Utilisateur connecté → token BDD
    - Guest → token session
    - Garde l'activité importée si existante
    """
    user, token = StravaService.get_token_from_session()
    print("DEBUG activities → user:", user, "token:", token)
    strava_connected = bool(token)
    activities = []

    if strava_connected:
        all_activities = StravaService.fetch_activities(token) or []
        # Filtre uniquement les courses
        activities = [act for act in all_activities if act.get("type") == "Run"]

    # Récupère l'activité importée en session (guest ou user)
    selected_activity = session.get("selected_activity")

    return render_template(
        "strava_activities.html",
        activities=activities,
        strava_connected=strava_connected,
        selected_activity=selected_activity
    )

@strava_bp.route("/import/<activity_id>")
def import_activity(activity_id):
    """Importer une activité Strava si token disponible, sinon guest ne fait rien."""
    user, token = StravaService.get_token_from_session()

    if not token:
        flash("Vous n'avez pas de compte Strava, l'import n'est pas disponible.", "info")
        return redirect(url_for("gobelet"))

    act = StravaService.fetch_activity(token, activity_id)
    if not act:
        flash("Impossible de récupérer l'activité.", "error")
        return redirect(url_for("strava.activities"))

    distance_km = act["distance"] / 1000
    total_seconds = act["moving_time"]

    pace = format_pace_mmss(total_seconds / distance_km) if distance_km > 0 else None
    time_str = (
        f"{total_seconds // 3600}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}"
        if total_seconds >= 3600 else
        f"{total_seconds // 60}:{total_seconds % 60:02d}"
    )

    selected = {
        "id": act["id"],
        "name": act["name"],
        "time": time_str,
        "distance": round(distance_km, 2),
        "pace": pace,
    }

    if "map" in act and act["map"].get("summary_polyline"):
        selected["polyline"] = act["map"]["summary_polyline"]

    session["selected_activity"] = selected
    flash("Activité importée pour personnalisation du gobelet ✅", "success")
    return redirect(url_for("gobelet"))

@strava_bp.route("/track/<activity_id>")
def track(activity_id):
    """Afficher tracé GPS si token disponible, sinon message guest."""
    user, token = StravaService.get_token_from_session()

    if not token:
        return "Pas de tracé GPS disponible pour les guests sans compte Strava.", 403

    activity = StravaService.fetch_activity(token, activity_id)
    if not activity or "map" not in activity or not activity["map"].get("summary_polyline"):
        return "Pas de tracé GPS disponible."

    coords = polyline.decode(activity["map"]["summary_polyline"])
    buf = render_track_image(coords, activity_id)

    return send_file(buf, mimetype="image/png", download_name=f"track_{activity_id}.png")

@strava_bp.route("/debug-strava")
def debug_strava():
    import os
    return {
        "STRAVA_CLIENT_ID": os.getenv("STRAVA_CLIENT_ID"),
        "STRAVA_REDIRECT_URI": os.getenv("STRAVA_REDIRECT_URI"),
        "Flask_config_CLIENT_ID": current_app.config.get("STRAVA_CLIENT_ID"),
        "Flask_config_REDIRECT_URI": current_app.config.get("STRAVA_REDIRECT_URI"),
    }



