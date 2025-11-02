from flask import Blueprint, request, session, redirect, url_for, render_template, flash
from werkzeug.security import check_password_hash, generate_password_hash
from models.db_database import db, User, Order
from services.strava_service import StravaService, decrement_strava_connections

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    # NOTE: Nous FORÇONS la réautorisation Strava à chaque login en nettoyant
    # les tokens Strava côté user si présents. L'utilisateur devra reconnecter via /connect.
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()
        if not user or not user.password or not check_password_hash(user.password, password):
            flash("Identifiants incorrects.", "error")
            return redirect(url_for("auth.login"))

        session["user_id"] = user.id
        flash("Connexion réussie ✅ — vous devrez reconnecter Strava si vous souhaitez importer des activités.", "success")
        print(f"👤 Utilisateur {user.email} connecté (id={user.id})")

        # --- FORCER de-reconnect Strava pour éviter reconnexions automatiques "fantômes"
        # On vérifie s'il y a des tokens stockés côté user (access/refresh) : si oui, on les supprime proprement.
        try:
            if user.strava_access_token or user.strava_refresh_token:
                print(f"🔒 Nettoyage tokens Strava existants pour user {user.id} (obligé pour forcer réauth).")
                # Révoque côté Strava et nettoie en base
                StravaService.disconnect_user(user)
                # Décrémente le compteur global si on supprimait réellement quelque chose
                decrement_strava_connections()
                StravaService.recalculate_connected_count()
                print(f"✅ Tokens Strava supprimés et compteur décrémenté pour user {user.id}")
        except Exception as e:
            # Ne doit pas empêcher la connexion si quelque chose casse côté API Strava
            print(f"⚠️ Erreur en nettoyant tokens Strava pour user {user.id}: {e}")

        # IMPORTANT : on ne tente PAS d'auto-refresh Strava ici — l'utilisateur doit repasser par le flow OAuth.
        return redirect(url_for("auth.compte"))

    return render_template("login.html")

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    message, message_type = None, None

    if request.method == "POST":
        first_name = request.form["first_name"]
        last_name = request.form["last_name"]
        email = request.form["email"]
        password = request.form["password"]

        existing_user = User.query.filter_by(email=email).first()

        if existing_user:
            if existing_user.password is None:
                # ⚡ Cas 1 : transformation d’un compte guest en vrai compte
                existing_user.first_name = first_name
                existing_user.last_name = last_name
                existing_user.password = generate_password_hash(password, method='pbkdf2:sha256')
                db.session.commit()

                # 🧩 Si le guest avait une session Strava active, on la supprime proprement
                StravaService.migrate_guest_to_user(existing_user)

                message = "Votre compte a été activé avec succès. Vous pouvez maintenant vous connecter."
                message_type = "success"
            else:
                # Un compte normal existe déjà avec cet email
                message = "Un compte existe déjà avec cet email."
                message_type = "error"

        else:
            # ⚙️ Cas 2 : création d’un nouvel utilisateur normal
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
            new_user = User(
                first_name=first_name,
                last_name=last_name,
                email=email,
                password=hashed_password
            )
            db.session.add(new_user)
            db.session.commit()

            # 🧩 Si un guest Strava est actif dans la session, on le supprime ici aussi
            StravaService.migrate_guest_to_user(new_user)

            message = "Compte créé avec succès, vous pouvez vous connecter."
            message_type = "success"

    return render_template("register.html", message=message, message_type=message_type)

@auth_bp.route("/logout")
def logout():
    """
    Déconnecte l’utilisateur du site ET de Strava proprement :
    - Révoque le token Strava (user ou guest)
    - Nettoie la session
    - Met à jour le compteur global
    """
    from services.strava_service import StravaService

    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user:
            print(f"🔒 Déconnexion utilisateur {user.email} (id={user.id})...")

            # Révoque proprement le token Strava s’il existe
            if user.strava_access_token:
                StravaService.disconnect_user(user)
                print(f"✅ Strava déconnecté pour user {user.id}")
            else:
                print(f"⚠️ Aucun token Strava actif pour user {user.id}")

        # Nettoyage session utilisateur Flask
        session.pop("user_id", None)
        session.pop("selected_activity", None)
        session["strava_just_disconnected"] = True

    else:
        # Cas GUEST
        print("🔒 Déconnexion d’un invité (guest)...")
        StravaService.disconnect_session_principal()

    # 🔄 Recalcule le compteur global
    StravaService.recalculate_connected_count()

    flash("Déconnexion réussie ✅ (Strava déconnecté et session effacée)", "success")
    print("👋 Utilisateur déconnecté avec succès — session nettoyée.")
    return redirect(url_for("auth.login"))


@auth_bp.route("/compte")
def compte():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user = User.query.get(session["user_id"])
    if not user:
        return redirect(url_for("auth.login"))
    # Récupérer toutes les commandes de l'utilisateur
    orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()

    return render_template("compte.html", user=user, orders=orders)
