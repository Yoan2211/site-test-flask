# services/user_service.py
from models.db_database import db, User

def get_all_users():
    """Retourne tous les utilisateurs."""
    return User.query.all()

def get_user_by_id(user_id):
    """Retourne un utilisateur par son ID."""
    return User.query.get(user_id)

def create_user(first_name, email):
    """Crée un utilisateur minimal (ex: API test)."""
    user = User(first_name=first_name, email=email)
    db.session.add(user)
    db.session.commit()
    return user
