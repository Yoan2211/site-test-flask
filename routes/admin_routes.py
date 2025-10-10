# routes/admin_routes.py
import os
from flask import Blueprint, request, redirect, url_for, session, render_template, flash, current_app
import requests
from datetime import datetime
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from models.db_database import db, User, Order, AppStats, GuestStravaSession
from services.strava_service import decrement_strava_connections, StravaService

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

        from models.db_database import AppStats, User, GuestStravaSession

        # Comptage des utilisateurs connectés à Strava
        user_count = User.query.filter(
            User.strava_access_token.isnot(None),
            User.strava_access_token != ""
        ).count()

        # Comptage des sessions invitées
        guest_count = GuestStravaSession.query.count()

        # Total global
        connected_count = user_count + guest_count

        # Timestamp pour affichage
        from datetime import datetime
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        return self.render(
            "admin_index.html",
            connected_count=connected_count,
            user_count=user_count,
            guest_count=guest_count,
            timestamp=timestamp
        )

    def is_accessible(self):
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

