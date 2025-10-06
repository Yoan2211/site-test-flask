# routes/user_routes.py
from flask import Blueprint, jsonify, request
from services.user_service import get_all_users, create_user

user_bp = Blueprint("user_bp", __name__)

@user_bp.route("/api/users", methods=["GET"])
def list_users():
    users = get_all_users()
    return jsonify([{"id": u.id, "name": u.first_name, "email": u.email} for u in users])

@user_bp.route("/api/users", methods=["POST"])
def add_user():
    data = request.get_json()
    name = data.get("name")
    email = data.get("email")
    new_user = create_user(name, email)
    return jsonify({"id": new_user.id, "name": new_user.name, "email": new_user.email}), 201
