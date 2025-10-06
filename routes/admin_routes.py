# routes/admin_routes.py
import os
from flask import Blueprint, request, redirect, url_for, session, render_template, flash
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from models.db_database import db, User, Order

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
        return super().index()

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
# ⚙️ Initialisation de l’interface Flask-Admin
# ==========================================================
def init_admin(app):
    # ⚠️ endpoint renommé pour éviter le conflit "admin"
    admin_ui = Admin(
        app,
        name="RunCup Admin",
        template_mode="bootstrap3",
        index_view=SecureAdminIndexView(),
        endpoint="admin_ui",
    )

    # Ajout des tables à gérer dans le panneau admin
    admin_ui.add_view(SecureModelView(User, db.session))
    admin_ui.add_view(SecureModelView(Order, db.session))
