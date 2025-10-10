from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

# ------------------ Utilisateurs ------------------
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True)  # 🔑 mot de passe haché
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 🔥 Ajout pour Strava
    strava_access_token = db.Column(db.String(255), nullable=True)
    strava_refresh_token = db.Column(db.String(255), nullable=True)
    strava_token_expires_at = db.Column(db.Integer, nullable=True)  # timestamp Unix

    orders = db.relationship("Order", backref="user", lazy=True)
    billing_info = db.relationship("BillingInfo", backref="user", uselist=False, lazy=True)

    def __repr__(self):
        return f"<User {self.email}>"


# ------------------ Infos de facturation ------------------
class BillingInfo(db.Model):
    __tablename__ = "billing_info"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    street = db.Column(db.String(100))
    postal_code = db.Column(db.String(10))
    city = db.Column(db.String(50))
    region = db.Column(db.String(10))   # canton
    country = db.Column(db.String(10))  # ex: CH
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<BillingInfo {self.user_id} - {self.first_name} {self.last_name}>"


# ------------------ Commandes ------------------
class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(64), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=False, default="CHF")
    status = db.Column(db.String(20), nullable=False, default="pending")
    payment_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Snapshot des infos de facturation au moment de la commande
    billing_first_name = db.Column(db.String(50))
    billing_last_name = db.Column(db.String(50))
    billing_email = db.Column(db.String(150))
    billing_street = db.Column(db.String(100))
    billing_postal_code = db.Column(db.String(10))
    billing_city = db.Column(db.String(50))
    billing_region = db.Column(db.String(10))
    billing_country = db.Column(db.String(10))
    processed = db.Column(db.Boolean, default=False)  # <-- ajouter cette colonne

    photos = db.relationship("OrderPhoto", backref="order", lazy=True)

    def __repr__(self):
        return f"<Order {self.order_number} - {self.status}>"

    @classmethod
    def create_with_billing(cls, user=None, amount=0.0, billing_data=None, currency="CHF", status="pending", order_number=None):
        """
        Crée une commande :
        - Si user est fourni, utilise ses infos de facturation.
        - Si user est None, utilise billing_data dict.
        """
        if not order_number:
            # fallback si rien n'est fourni
            order_number = f"ORD{int(datetime.utcnow().timestamp())}"

        if user:
            billing = user.billing_info
            order = cls(
                user_id=user.id,
                order_number=order_number,
                amount=amount,
                currency=currency,
                status=status,
                billing_first_name=billing.first_name if billing else user.first_name,
                billing_last_name=billing.last_name if billing else user.last_name,
                billing_email=billing.email if billing else user.email,
                billing_street=billing.street if billing else None,
                billing_postal_code=billing.postal_code if billing else None,
                billing_city=billing.city if billing else None,
                billing_region=billing.region if billing else None,
                billing_country=billing.country if billing else None
            )
        else:
            # Pour les guests, billing_data doit être un dict avec les infos de facturation
            order = cls(
                user_id=None,
                order_number=order_number,
                amount=amount,
                currency=currency,
                status=status,
                billing_first_name=billing_data.get("billing_firstname"),
                billing_last_name=billing_data.get("billing_lastname"),
                billing_email=billing_data.get("billing_email"),
                billing_street=billing_data.get("billing_address"),
                billing_postal_code=billing_data.get("billing_postal"),
                billing_city=billing_data.get("billing_city"),
                billing_region=billing_data.get("billing_canton"),
                billing_country=billing_data.get("billing_country")
            )

        db.session.add(order)
        db.session.commit()
        return order


# ------------------ Photos de commande ------------------
class OrderPhoto(db.Model):
    __tablename__ = "order_photos"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    photo_url = db.Column(db.Text, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<OrderPhoto {self.id} - Order {self.order_id}>"

class AppStats(db.Model):
    __tablename__ = "app_stats"
    id = db.Column(db.Integer, primary_key=True)
    strava_connected_count = db.Column(db.Integer, default=0)

class GuestStravaSession(db.Model):
    __tablename__ = "guest_strava_sessions"

    id = db.Column(db.Integer, primary_key=True)
    guest_id = db.Column(db.String(64), unique=True, nullable=False)  # identifiant aléatoire
    strava_access_token = db.Column(db.String(255), nullable=False)
    strava_refresh_token = db.Column(db.String(255), nullable=True)
    strava_token_expires_at = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<GuestStravaSession {self.guest_id}>"

