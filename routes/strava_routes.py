from flask import Blueprint, redirect, request, session, flash, url_for, render_template, current_app, send_file
from models.db_database import db, User, GuestStravaSession
from services.strava_service import StravaService
from services.strava_service import increment_strava_connections, decrement_strava_connections
from utils.tts_utils import format_pace_mmss
from utils.image_utils import render_track_image
import polyline
import requests
import uuid

strava_bp = Blueprint("strava", __name__)

# ----------------- Logs Strava -----------------

# ==========================================================
# 🚴 Connexion à Strava
# ==========================================================
@strava_bp.route("/connect")
def connect():
    """
    Redirige vers Strava pour autorisation, 
    en respectant le quota local (users + guests ≤ 999)
    """
    from services.strava_service import StravaService
    from flask import flash, redirect, url_for

    StravaService.cleanup_expired_connections()
    # Vérifie le quota global (users + guests)
    current_total = StravaService.recalculate_connected_count()
    if current_total >= 999:
        flash("⚠️ Limite maximale d'athlètes atteinte (999). Veuillez réessayer plus tard.", "warning")
        print(f"🚫 Connexion Strava refusée : quota atteint ({current_total}/999).")
        return redirect(url_for("gobelet"))

    # Force Strava à redemander l’autorisation à chaque fois
    auth_url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={current_app.config['STRAVA_CLIENT_ID']}"
        f"&response_type=code"
        f"&redirect_uri={current_app.config['STRAVA_REDIRECT_URI']}"
        f"&approval_prompt=force"
        f"&scope=activity:read"
    )

    print(f"➡️ Redirection vers Strava (quota OK : {current_total}/999)")
    return redirect(auth_url)


# ==========================================================
# 🔑 Callback OAuth Strava
# ==========================================================
@strava_bp.route("/authorized")
def authorized():
    import requests, time, uuid
    from models.db_database import GuestStravaSession
    from services.strava_service import (
        increment_strava_connections,
        StravaService,
    )

    # 🧹 Nettoyage avant toute nouvelle connexion
    StravaService.cleanup_expired_connections()

    # Vérifie le quota avant d'aller plus loin
    current_total = StravaService.recalculate_connected_count()
    if current_total >= 999:
        flash("⚠️ Limite maximale d'athlètes atteinte (999). Veuillez réessayer plus tard.", "warning")
        print(f"🚫 Autorisation refusée : quota atteint ({current_total}/999).")
        return redirect(url_for("gobelet"))

    # --- Code Strava reçu ---
    code = request.args.get("code")
    if not code:
        flash("Erreur lors de la connexion Strava.", "error")
        return redirect(url_for("gobelet"))

    token_data = StravaService.exchange_code_for_token(code)
    if not token_data or not token_data.get("access_token"):
        flash("Impossible de récupérer le token Strava.", "error")
        return redirect(url_for("gobelet"))

    strava_token = token_data["access_token"]
    strava_refresh = token_data["refresh_token"]
    strava_expires = token_data["expires_at"]
    pending_activity = session.get("selected_activity")

    already_connected = False

    # --- Cas USER connecté ---
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user:
             # 👇 Fusion automatique d’une session guest Strava existante
            StravaService.migrate_guest_to_user(user)
            if user.strava_access_token == strava_token:
                already_connected = True
                print(f"⚠️ Strava déjà lié pour user {user.id}, pas d'incrément.")
            else:
                # Révoque l'ancien token s'il existe
                if user.strava_access_token:
                    try:
                        r = requests.post(
                            "https://www.strava.com/oauth/deauthorize",
                            headers={"Authorization": f"Bearer {user.strava_access_token}"},
                            timeout=5,
                        )
                        print(f"🧹 Ancien token Strava révoqué pour user {user.id} ({r.status_code})")
                    except Exception as e:
                        print(f"⚠️ Erreur révocation Strava user {user.id}: {e}")

                user.strava_access_token = strava_token
                user.strava_refresh_token = strava_refresh
                user.strava_token_expires_at = strava_expires
                db.session.commit()
                print(f"🔑 Nouveau token Strava enregistré pour user {user.id}")

            # Nettoyage de session
            for k in ["strava_token", "strava_refresh_token", "strava_expires_at"]:
                session.pop(k, None)

            if pending_activity:
                session["selected_activity"] = pending_activity

            flash("Compte Strava lié à votre profil ✅", "success")

    # --- Cas GUEST ---
    else:
        guest_id = session.get("guest_id") or str(uuid.uuid4())
        session["guest_id"] = guest_id
        session["strava_token"] = strava_token
        session["strava_refresh_token"] = strava_refresh
        session["strava_expires_at"] = strava_expires

        entry = GuestStravaSession.query.filter_by(guest_id=guest_id).first()
        if not entry:
            entry = GuestStravaSession(
                guest_id=guest_id,
                strava_access_token=strava_token,
                strava_refresh_token=strava_refresh,
                strava_token_expires_at=strava_expires,
            )
            db.session.add(entry)
        else:
            entry.strava_access_token = strava_token
            entry.strava_refresh_token = strava_refresh
            entry.strava_token_expires_at = strava_expires
        db.session.commit()

        flash("Strava connecté en mode invité ✅", "success")

    # --- Mise à jour compteur ---
    skip_increment = session.pop("skip_strava_increment_once", False)
    if not already_connected and not skip_increment:
        increment_strava_connections()
        StravaService.recalculate_connected_count()


    return redirect(url_for("strava.activities"))






# ==========================================================
# 🔒 Déconnexion Strava
# ==========================================================
@strava_bp.route("/disconnect", methods=["POST"])
def disconnect():
    """Déconnexion Strava (libère les slots d'athlètes connectés pour user ou guest)."""
    from models.db_database import GuestStravaSession
    from services.strava_service import (
        decrement_strava_connections,
        StravaService,
    )
    import requests

    user = None
    token_cleared = False

    # 🔹 Cas utilisateur connecté
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user and (user.strava_access_token or user.strava_refresh_token):
            try:
                # 🔸 Révocation côté Strava (access_token)
                if user.strava_access_token:
                    r = requests.post(
                        "https://www.strava.com/oauth/deauthorize",
                        headers={"Authorization": f"Bearer {user.strava_access_token}"},
                        timeout=5,
                    )
                    print(f"🔒 Strava user {user.id} → status {r.status_code}")
            except Exception as e:
                print(f"⚠️ Erreur déconnexion Strava user {user.id}: {e}")

            # 🔸 Nettoyage local complet
            user.strava_access_token = None
            user.strava_refresh_token = None   # ← ✅ suppression du refresh_token !
            user.strava_token_expires_at = None
            db.session.commit()
            print(f"🧹 Tokens Strava effacés pour user {user.id}")

            # 🔸 Mise à jour compteur
            decrement_strava_connections()
            StravaService.recalculate_connected_count()

            token_cleared = True

    # 🔹 Cas guest connecté via session
    elif "strava_token" in session:
        guest_token = session.get("strava_token")
        guest_id = session.get("guest_id")

        # 🔸 Révocation côté Strava
        if guest_token:
            try:
                r = requests.post(
                    "https://www.strava.com/oauth/deauthorize",
                    headers={"Authorization": f"Bearer {guest_token}"},
                    timeout=5,
                )
                print(f"🔒 Strava guest {guest_id or '?'} → status {r.status_code}")
            except Exception as e:
                print(f"⚠️ Erreur déconnexion Strava (guest): {e}")

        # 🔸 Suppression de la session invitée dans la base
        if guest_id:
            GuestStravaSession.query.filter_by(guest_id=guest_id).delete()
            db.session.commit()

        # 🔸 Nettoyage complet de la session Flask
        for key in [
            "guest_id",
            "strava_token",
            "strava_refresh_token",
            "strava_expires_at",
            "selected_activity",
        ]:
            session.pop(key, None)

        # 🔸 Mise à jour compteur
        decrement_strava_connections()
        StravaService.recalculate_connected_count()

        token_cleared = True

    # 🔹 Indicateur pour le template
    session["strava_just_disconnected"] = True

    # 🔹 Message utilisateur
    if token_cleared:
        flash("Compte Strava déconnecté ✅", "success")
    else:
        flash("Aucun compte Strava à déconnecter.", "info")

    # 🔹 Affichage de l'état final des tokens (debug)
    if user is not None:
        print(f"🧠 État final user {user.id} → access={user.strava_access_token}, refresh={user.strava_refresh_token}")
    else:
        print("🧠 Aucun user trouvé dans la session (probablement invité ou session expirée)")

    return redirect(url_for("gobelet"))





# ----------------- Activités Strava -----------------
# ==========================================================
# 🏃 Liste des activités Strava
# ==========================================================

@strava_bp.route("/activities")
def activities():
    """
    Affiche les activités Strava avec pagination et filtres.
    """
    user, token = StravaService.get_token_from_session()
    strava_connected = bool(token)
    activities = []
    per_page = 20
    page = int(request.args.get("page", 1))

    # Filtres depuis l'URL
    search = request.args.get("search", "").strip().lower()
    min_distance_str = request.args.get("min_distance", "").strip()
    max_distance_str = request.args.get("max_distance", "").strip()

    min_distance = float(min_distance_str) if min_distance_str else 0
    max_distance = float(max_distance_str) if max_distance_str else 0

    activity_type = request.args.get("type", "Run")

    if strava_connected:
        all_activities = StravaService.fetch_all_activities(token, max_pages=5, per_page=100) or []
        runs = [act for act in all_activities if act.get("type") == activity_type]

        # Appliquer les filtres
        filtered = []
        for act in runs:
            dist_km = act["distance"] / 1000
            name = act["name"].lower()
            if search and search not in name:
                continue
            if min_distance and dist_km < min_distance:
                continue
            if max_distance and dist_km > max_distance:
                continue
            filtered.append(act)

        # Pagination
        total = len(filtered)
        start = (page - 1) * per_page
        end = start + per_page
        activities = filtered[start:end]

        has_prev = page > 1
        has_next = end < total

    else:
        has_prev = has_next = False
        total = 0
        search = ""
        activity_type = "Run"

    selected_activity = session.get("selected_activity")

    return render_template(
        "strava_activities.html",
        activities=activities,
        strava_connected=strava_connected,
        selected_activity=selected_activity,
        page=page,
        has_prev=has_prev,
        has_next=has_next,
        search=search,
        min_distance=min_distance,
        max_distance=max_distance,
        activity_type=activity_type
    )


# ==========================================================
# 🏁 Import d’une activité
# ==========================================================
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

# ==========================================================
# 🗺️ Tracé GPS
# ==========================================================
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

# ==========================================================
# 🧰 Debug (utile en dev)
# ==========================================================
@strava_bp.route("/debug-strava")
def debug_strava():
    import os
    return {
        "STRAVA_CLIENT_ID": os.getenv("STRAVA_CLIENT_ID"),
        "STRAVA_REDIRECT_URI": os.getenv("STRAVA_REDIRECT_URI"),
        "Flask_config_CLIENT_ID": current_app.config.get("STRAVA_CLIENT_ID"),
        "Flask_config_REDIRECT_URI": current_app.config.get("STRAVA_REDIRECT_URI"),
    }



