from flask import Blueprint, request, session, redirect, url_for, render_template, flash
from werkzeug.security import check_password_hash, generate_password_hash
from models.db_database import db
from models.db_database import User
from models.db_database import Order

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    message, message_type = None, None

    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()

        if not user:
            # Aucun compte avec cet email
            message = "Aucun compte n'est associé à cette adresse email."
            message_type = "error"
        elif not user.password:
            # Compte guest (créé automatiquement après une commande)
            message = "Cet email correspond à un compte invité créé lors d'une commande. Veuillez créer un compte pour vous connecter."
            message_type = "error"
        elif check_password_hash(user.password, password):
            # Connexion réussie
            session["user_id"] = user.id
            return redirect(url_for("auth.compte"))
        else:
            # Mauvais mot de passe
            message = "Email ou mot de passe incorrect."
            message_type = "error"

    return render_template("login.html", message=message, message_type=message_type)

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
                # ⚡ Transformation guest -> vrai compte
                existing_user.first_name = first_name
                existing_user.last_name = last_name
                existing_user.password = generate_password_hash(password, method='pbkdf2:sha256')
                db.session.commit()

                message = "Votre compte a été activé avec succès. Vous pouvez maintenant vous connecter."
                message_type = "success"
            else:
                # Un compte normal existe déjà avec cet email
                message = "Un compte existe déjà avec cet email."
                message_type = "error"
        else:
            # Cas normal : création d’un nouveau compte
            hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
            new_user = User(
                first_name=first_name,
                last_name=last_name,
                email=email,
                password=hashed_password
            )
            db.session.add(new_user)
            db.session.commit()

            message = "Compte créé avec succès, vous pouvez vous connecter."
            message_type = "success"

    return render_template("register.html", message=message, message_type=message_type)

@auth_bp.route("/logout")
def logout():
    session.pop("user_id", None)
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