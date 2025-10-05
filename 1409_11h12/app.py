# app.py

import os
import uuid
import qrcode
from io import BytesIO
import base64
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_FOLDER = os.path.join(STATIC_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_ROUTE_EXT = {".svg", ".png", ".jpg", ".jpeg", ".gpx"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB

PRICES = {
    "BASE": 12.9,
    "RESULTS": 5.0,
    "ROUTE": 7.0,
}

# ----------------- Panier -----------------
def get_cart_items():
    items = session.get("cart_items")
    if items is None:
        items = []
        session["cart_items"] = items
    return items

def add_cart_item(item):
    items = get_cart_items()
    for it in items:
        if it.get("sku") == item.get("sku") \
           and it.get("color") == item.get("color") \
           and it.get("options") == item.get("options"):
            it["qty"] += item["qty"]
            it["total"] = round(it["unit_price"] * it["qty"], 2)
            session["cart_items"] = items
            return
    items.append(item)
    session["cart_items"] = items

def cart_total():
    items = get_cart_items()
    return round(sum(i.get("unit_price", 0.0) * i.get("qty", 1) for i in items), 2)

def save_route_file(f):
    if not f or f.filename.strip() == "":
        return None
    name = secure_filename(f.filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_ROUTE_EXT:
        return None
    new_name = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(app.config["UPLOAD_FOLDER"], new_name)
    f.save(path)
    return f"uploads/{new_name}"


# ----------------- Routes -----------------
@app.route("/")
def home():
    return redirect(url_for("gobelet"))

@app.route("/gobelet", methods=["GET", "POST"])
def gobelet():
    if request.method == "POST":
        color = request.form.get("color", "blanc")
        try:
            qty = max(1, int(request.form.get("qty", "1")))
        except ValueError:
            qty = 1

        add_results = request.form.get("add_results") == "on"
        add_route = request.form.get("add_route") == "on"

        results_data = None
        if add_results:
            results_data = {
                "course_name": request.form.get("course_name", "").strip(),
                "course_date": request.form.get("course_date", "").strip(),
                "bib": request.form.get("bib", "").strip(),
                "time": request.form.get("time", "").strip(),
                "rank": request.form.get("rank", "").strip(),
                "distance": request.form.get("distance", "").strip(),
            }

        route_path = None
        route_name = request.form.get("route_name", "").strip()
        if add_route:
            route_file = request.files.get("route_file")
            route_rel = save_route_file(route_file)
            if not route_rel:
                flash("Format de fichier non supporté (SVG/PNG/JPG/GPX) ou fichier manquant.", "error")
                return redirect(url_for("gobelet"))
            route_path = route_rel

        unit = PRICES["BASE"] + (PRICES["RESULTS"] if add_results else 0.0) + (PRICES["ROUTE"] if add_route else 0.0)
        total = round(unit * qty, 2)

        item = {
            "id": uuid.uuid4().hex,
            "sku": "gobelet",
            "name": "Gobelet personnalisé",
            "image": "img/gobelet.jpg",
            "color": color,
            "qty": qty,
            "unit_price": round(unit, 2),
            "total": total,
            "options": {
                "add_results": add_results,
                "results": results_data,
                "add_route": add_route,
                "route_path": route_path,
                "route_name": route_name,
            },
        }
        add_cart_item(item)
        return redirect(url_for("cart_view"))

    return render_template("products2.html")

@app.route("/cart")
def cart_view():
    items = get_cart_items()
    total = cart_total()
    return render_template("cart.html", items=items, total=total)

@app.post("/remove-item/<item_id>")
def remove_item(item_id):
    items = [it for it in get_cart_items() if it.get("id") != item_id]
    session["cart_items"] = items
    return redirect(url_for("cart_view"))

@app.post("/clear-cart")
def clear_cart():
    session["cart_items"] = []
    return redirect(url_for("cart_view"))


# ----------------- Paiement Twint simplifié -----------------
@app.post("/checkout")
def checkout():
    items = get_cart_items()
    if not items:
        flash("Le panier est vide.", "error")
        return redirect(url_for("cart_view"))

    total = cart_total()
    order_id = uuid.uuid4().hex

    # 🔹 Exemple d’URL Twint fictive (ici juste pour test)
    twint_payment_url = f"twint://pay?amount={int(total*100)}&currency=CHF&ref={order_id}"

    # Générer un QR code base64
    qr = qrcode.make(twint_payment_url)
    buffer = BytesIO()
    qr.save(buffer, format="PNG")
    qr_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    order = {
        "id": order_id,
        "items": items,
        "total": total,
        "currency": "CHF",
        "payment_url": twint_payment_url,
        "qrcode_url": f"data:image/png;base64,{qr_b64}",
    }
    session["last_order"] = order

    return render_template("twint.html", order=order, qrcode_url=order["qrcode_url"])


@app.route("/payment/success", methods=["GET"])
def payment_success():
    order = session.get("last_order")
    if not order:
        flash("Commande introuvable.", "error")
        return redirect(url_for("cart_view"))

    flash("Paiement confirmé via Twint ✅ Merci !", "success")
    session["cart_items"] = []  # vider le panier
    return render_template("success.html", order=order)


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
