# routes/admin_routes.py
import os
from flask import Blueprint, request, redirect, url_for, session, render_template, flash, current_app
import requests
from datetime import datetime
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from models.db_database import db, User, Order
from services.strava_service import StravaService

admin_bp = Blueprint("admin_bp", __name__)

# --- Variables d’environnement ---
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ALLOWED_IP = os.getenv("ADMIN_ALLOWED_IP")

# ==========================================================
# 🔒 Sécurité pour le panneau Flask-Admin
# ==========================================================
class SecureAdminIndexView(AdminIndexView):
    @expose("/")
    def index(self):
        if not session.get("logged_in"):
            return redirect(url_for("admin_bp.admin_login"))

        # 🧮 Récupère le compteur global depuis AppStats
        from models.db_database import AppStats
        stats = AppStats.query.first()
        connected_count = stats.strava_connected_count if stats else 0
        return self.render("admin_index.html", connected_count=connected_count)

    def is_accessible(self):
        # ⛔ On retire le filtrage IP (Render masque souvent ton IP)
        return session.get("logged_in", False)


class SecureModelView(ModelView):
    def is_accessible(self):
        return session.get("logged_in", False)


# ==========================================================
# 🔑 Routes de connexion / déconnexion admin
# ==========================================================
@admin_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            flash("Connexion réussie ✅", "success")
            return redirect("/admin")
        else:
            flash("Identifiants invalides ❌", "danger")
    return render_template("admin_login.html")


@admin_bp.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    flash("Déconnecté 🔒", "info")
    return redirect(url_for("admin_bp.admin_login"))



# ==========================================================
# 🧹 Nettoyage Strava : libère les athlètes connectés
# ==========================================================
@admin_bp.route("/admin/strava/cleanup", methods=["POST"])
def cleanup_strava_tokens():
    """Libère les athlètes connectés si plus de 999"""
    if not session.get("logged_in"):
        flash("Accès refusé. Veuillez vous connecter.", "danger")
        return redirect(url_for("admin_bp.admin_login"))

    # 🔹 Récupération des utilisateurs connectés à Strava
    connected_users = User.query.filter(User.strava_access_token.isnot(None)).all()
    total_connected = len(connected_users)

    if total_connected <= 999:
        flash(f"{total_connected} athlètes connectés — pas besoin de nettoyage ✅", "info")
        return redirect("/admin")

    # 🔸 Sélection des utilisateurs à déconnecter
    excess_users = connected_users[999:]  # on garde les 999 premiers
    count_disconnected = 0

    for user in excess_users:
        try:
            requests.post(
                "https://www.strava.com/oauth/deauthorize",
                headers={"Authorization": f"Bearer {user.strava_access_token}"},
                timeout=5
            )
        except Exception as e:
            print(f"Erreur déconnexion Strava user {user.id}: {e}")

        # Supprime uniquement l'access token (garde le refresh_token et l'athlete_id)
        user.strava_access_token = None
        user.strava_token_expires_at = None
        count_disconnected += 1
        StravaService.decrement_strava_connections()

    db.session.commit()
    flash(f"✅ {count_disconnected} athlètes déconnectés pour repasser sous la limite de 999.", "success")
    return redirect("/admin")
@admin_bp.route("/admin/strava/clear_all", methods=["POST"])
def clear_all_strava_tokens():
    """Supprime tous les tokens Strava (libère tous les athlètes connectés)"""
    if not session.get("logged_in"):
        flash("Accès refusé. Veuillez vous connecter.", "danger")
        return redirect(url_for("admin_bp.admin_login"))

    from services.strava_service import decrement_strava_connections
    from models.db_database import AppStats

    # Récupère tous les utilisateurs ayant un token Strava actif
    users_with_token = User.query.filter(User.strava_access_token.isnot(None)).all()
    total_to_clear = len(users_with_token)
    count_cleared = 0

    for user in users_with_token:
        try:
            # 🔸 Révocation côté Strava
            requests.post(
                "https://www.strava.com/oauth/deauthorize",
                headers={"Authorization": f"Bearer {user.strava_access_token}"},
                timeout=5
            )
        except Exception as e:
            print(f"Erreur déconnexion Strava user {user.id}: {e}")

        # 🔸 Suppression des tokens locaux (on garde refresh_token)
        user.strava_access_token = None
        user.strava_token_expires_at = None
        count_cleared += 1

    db.session.commit()

    # 🔸 Réinitialise le compteur global AppStats
    stats = AppStats.query.first()
    if not stats:
        stats = AppStats(strava_connected_count=0)
        db.session.add(stats)
    else:
        stats.strava_connected_count = 0
    db.session.commit()

    flash(f"✅ Tous les tokens Strava ({count_cleared}) ont été nettoyés.", "success")
    return redirect("/admin")


# ==========================================================
# ⚙️ Initialisation de l’interface Flask-Admin
# ==========================================================
def init_admin(app):
    admin_panel = Admin(
        app,
        name="RunCup Admin",
        template_mode="bootstrap3",
        index_view=SecureAdminIndexView(),  # On garde notre vue sécurisée
        endpoint="admin",  # ⚠️ revenir au endpoint par défaut
        url="/admin"       # facultatif, mais clair
    )

    # Ajout des modèles visibles dans l’interface
    admin_panel.add_view(SecureModelView(User, db.session))
    admin_panel.add_view(SecureModelView(Order, db.session))

