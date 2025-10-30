import time
import requests
from flask import current_app, session
from models.db_database import db, User, GuestStravaSession, AppStats



class StravaService:
    # ==========================================================
    # 🔐 Gestion des tokens utilisateurs (BDD)
    # ==========================================================
    # ==========================================================
    # 🔐 Gestion des tokens utilisateurs (BDD)
    # ==========================================================
    @staticmethod
    def get_token(user, auto_refresh=False):
        """
        Récupère un token Strava valide :
        - auto_refresh=False (par défaut) → lecture simple, sans appel API
        - auto_refresh=True → autorise un refresh si l'utilisateur est encore activement lié à Strava

        ⚠️ Version avec logs détaillés pour traçage :
        - Indique quand aucun refresh n’est tenté.
        - Permet de vérifier que le code ne contacte plus Strava après un /disconnect.
        """

        import requests
        import time
        from flask import current_app
        from models.db_database import db

        if not user:
            print("[get_token] 🚫 Aucun user fourni → return None")
            return None

        # Aucun token actif → pas de refresh
        if not user.strava_access_token and not user.strava_refresh_token:
            print(f"[get_token] 🚫 Aucun token Strava pour user {getattr(user, 'id', '?')} (probablement déconnecté)")
            return None

        # Access token encore valide → simple retour
        if user.strava_token_expires_at and user.strava_token_expires_at > time.time():
            print(f"[get_token] ✅ Token Strava encore valide pour user {user.id} (expire à {user.strava_token_expires_at})")
            return user.strava_access_token

        # Token expiré mais pas d’auto-refresh demandé
        if not auto_refresh:
            print(f"[get_token] ⏸ Token expiré pour user {user.id}, auto_refresh=False → aucun appel Strava")
            return None

        # Tentative de refresh (auto_refresh=True explicitement)
        print(f"[get_token] 🔁 Tentative de refresh Strava pour user {user.id}...")

        try:
            resp = requests.post(
                "https://www.strava.com/oauth/token",
                data={
                    "client_id": current_app.config["STRAVA_CLIENT_ID"],
                    "client_secret": current_app.config["STRAVA_CLIENT_SECRET"],
                    "grant_type": "refresh_token",
                    "refresh_token": user.strava_refresh_token,
                },
                timeout=5,
            )

            token_data = resp.json()

            if resp.status_code != 200 or "access_token" not in token_data:
                print(f"[get_token] ❌ Échec refresh ({resp.status_code}) : {token_data}")
                if "invalid_grant" in str(token_data):
                    user.strava_access_token = None
                    user.strava_refresh_token = None
                    user.strava_token_expires_at = None
                    db.session.commit()
                    print(f"[get_token] ⚠️ Token Strava révoqué → nettoyage complet pour user {user.id}")
                return None

            # Nouveau token OK → on met à jour
            user.strava_access_token = token_data["access_token"]
            user.strava_refresh_token = token_data.get("refresh_token", user.strava_refresh_token)
            user.strava_token_expires_at = token_data["expires_at"]
            db.session.commit()

            print(f"[get_token] ✅ Token Strava rafraîchi avec succès pour user {user.id}")
            return user.strava_access_token

        except Exception as e:
            print(f"[get_token] ⚠️ Erreur de refresh Strava pour user {user.id}: {e}")
            return None



    @staticmethod
    def disconnect_user(user):
        """
        Révoque proprement le token Strava d’un utilisateur connecté
        et nettoie les champs en base.
        """
        import requests
        if not user or not user.strava_access_token:
            print("⚠️ Aucun token Strava à révoquer pour cet utilisateur.")
            return False

        try:
            r = requests.post(
                "https://www.strava.com/oauth/deauthorize",
                headers={"Authorization": f"Bearer {user.strava_access_token}"},
                timeout=5,
            )
            print(f"[Strava deauth] user {user.id} → status {r.status_code}")
        except Exception as e:
            print(f"⚠️ Erreur deauth user {user.id}: {e}")

        # 🧹 Nettoyage local des tokens
        user.strava_access_token = None
        user.strava_refresh_token = None
        user.strava_token_expires_at = None
        from models.db_database import db
        db.session.commit()
        print(f"🧹 Tokens Strava nettoyés pour user {user.id}")

        return True

    @staticmethod
    def refresh_token(user) -> str | None:
        """
        Rafraîchit le token Strava d’un utilisateur si expiré.
        Retourne le nouveau access_token ou None en cas d’erreur.
        Gère proprement les cas de refresh_token expiré ou invalide.
        """
        import time
        from datetime import datetime
        import requests
        from flask import current_app

        if not user:
            print("⚠️ Aucun utilisateur fourni à refresh_token()")
            return None

        if not user.strava_refresh_token:
            print(f"⛔ Aucun refresh_token présent pour user {user.id}, abandon refresh.")
            return None

        # 🚫 Si l'utilisateur n'a plus de token (après disconnect), on ne tente rien
        if not user.strava_access_token and not user.strava_refresh_token:
            print(f"⚠️ Aucun token Strava valide pour user {user.id}, skip refresh")
            return None

        now = time.time()

        # 🧠 Si le token actuel est encore valide, inutile de le rafraîchir
        if user.strava_access_token and user.strava_token_expires_at and user.strava_token_expires_at > now:
            return user.strava_access_token

        print(f"🔁 Tentative de refresh Strava pour user {user.id}...")

        try:
            response = requests.post(
                "https://www.strava.com/oauth/token",
                data={
                    "client_id": current_app.config["STRAVA_CLIENT_ID"],
                    "client_secret": current_app.config["STRAVA_CLIENT_SECRET"],
                    "grant_type": "refresh_token",
                    "refresh_token": user.strava_refresh_token,
                },
                timeout=5
            )

            if response.status_code != 200:
                print(f"❌ Échec refresh Strava ({response.status_code}): {response.text}")

                # 🔥 Cas typique : refresh_token invalidé après un disconnect
                if response.status_code == 400 and "invalid" in response.text.lower():
                    print(f"⚠️ Refresh token invalide pour user {user.id}, nettoyage local.")
                    user.strava_access_token = None
                    user.strava_refresh_token = None
                    user.strava_token_expires_at = None
                    db.session.commit()
                return None

            data = response.json()
            user.strava_access_token = data.get("access_token")
            user.strava_refresh_token = data.get("refresh_token", user.strava_refresh_token)
            user.strava_token_expires_at = data.get("expires_at")
            db.session.commit()

            print(f"✅ Token Strava rafraîchi pour user {user.id}, expire à {datetime.utcfromtimestamp(user.strava_token_expires_at)}")
            return user.strava_access_token

        except Exception as e:
            print(f"⚠️ Exception lors du refresh Strava: {e}")
            return None



    # ==========================================================
    # 🧩 Validation d’un token (évite les sessions zombies)
    # ==========================================================
    @staticmethod
    def token_is_valid(access_token: str) -> bool:
        """Teste si un access_token Strava est encore valide."""
        if not access_token:
            return False
        try:
            r = requests.get(
                "https://www.strava.com/api/v3/athlete",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=5,
            )
            return r.status_code == 200
        except Exception as e:
            print(f"⚠️ Vérif token Strava échouée: {e}")
            return False

    # ==========================================================
    # 🎯 Sélection du token actif (user ou guest)
    # ==========================================================
    @staticmethod
    def get_token_from_session():
        """Retourne (user, token actif) selon la session Flask, sans refresh intempestif."""
        import time
        from flask import session
        from models.db_database import User, db

        # 🛑 Pare-choc : ne pas rafraîchir immédiatement après une déconnexion
        if session.pop("strava_skip_refresh_once", False):
            return (None, None)

        user = None
        token = None

        # --- Cas user connecté ---
        if "user_id" in session:
            user = User.query.get(session["user_id"])
            if not user:
                return (None, None)

            # token actif ?
            if user.strava_access_token and user.strava_token_expires_at and user.strava_token_expires_at > time.time():
                token = user.strava_access_token
            else:
                # refresh uniquement si refresh_token valide
                if user.strava_refresh_token:
                    print(f"🔁 Tentative de refresh Strava pour user {user.id}...")
                    new_token = StravaService.refresh_token(user)
                    if new_token:
                        token = new_token
                    else:
                        print(f"⚠️ Refresh token invalide pour user {user.id}, nettoyage local.")
                        user.strava_access_token = None
                        user.strava_refresh_token = None
                        user.strava_token_expires_at = None
                        db.session.commit()
                else:
                    print(f"⛔ Aucun refresh_token présent pour user {user.id}, skip refresh.")

        # --- Cas guest ---
        elif "strava_token" in session:
            token = session.get("strava_token")
            exp = session.get("strava_expires_at", 0)
            if time.time() > exp:
                print("⏰ Token guest expiré, suppression.")
                for key in ["strava_token", "strava_refresh_token", "strava_expires_at"]:
                    session.pop(key, None)
                token = None

        return (user, token)


    @staticmethod
    def disconnect_session_principal():
        """
        Déconnecte proprement Strava pour le user OU le guest courant :
        - Révocation côté Strava via /oauth/deauthorize
        - Nettoyage des tokens
        - Décrément du compteur global
        """
        import time
        import requests
        from flask import session
        from models.db_database import db, User, GuestStravaSession
        from services.strava_service import decrement_strava_connections

        did_decrement = False

        # ====== USER connecté ======
        if "user_id" in session:
            user = User.query.get(session["user_id"])
            if user:
                token_to_revoke = user.strava_access_token

                # 🔄 si le token est expiré, on n’essaie PAS de le refresh
                if not token_to_revoke:
                    print(f"⚠️ Aucun token actif pour user {user.id}, skip deauthorize.")
                else:
                    try:
                        r = requests.post(
                            "https://www.strava.com/oauth/deauthorize",
                            headers={"Authorization": f"Bearer {token_to_revoke}"},
                            timeout=5,
                        )
                        print(f"[Strava deauth] user {user.id} → status {r.status_code}")
                    except Exception as e:
                        print(f"[Strava deauth] erreur {e}")

                # 🧹 Nettoyage complet : supprime refresh_token pour éviter reconnexion auto fantôme
                user.strava_access_token = None
                user.strava_refresh_token = None
                user.strava_token_expires_at = None
                db.session.commit()

                decrement_strava_connections()
                did_decrement = True

        # ====== GUEST connecté ======
        if (not did_decrement) and ("strava_token" in session or "guest_id" in session):
            guest_id = session.get("guest_id")
            guest_token = session.get("strava_token")

            if guest_token:
                try:
                    r = requests.post(
                        "https://www.strava.com/oauth/deauthorize",
                        headers={"Authorization": f"Bearer {guest_token}"},
                        timeout=5,
                    )
                    print(f"[Strava deauth] guest {guest_id or '?'} → status {r.status_code}")
                except Exception as e:
                    print(f"[Strava deauth] exception guest: {e}")

            # Suppression de la session invitée
            if guest_id:
                GuestStravaSession.query.filter_by(guest_id=guest_id).delete()
                db.session.commit()

            for key in ["guest_id", "strava_token", "strava_refresh_token", "strava_expires_at", "selected_activity"]:
                session.pop(key, None)

            decrement_strava_connections()
            did_decrement = True

        # ✅ Empêche un refresh auto juste après déconnexion
        session["strava_skip_refresh_once"] = True
        session["strava_just_disconnected"] = True

        # 🔄 Cohérence du compteur
        try:
            from services.strava_service import StravaService
            StravaService.recalculate_connected_count()
        except Exception:
            pass

        return did_decrement


    # ==========================================================
    # 🔧 Utilitaire : purge propre de la session Flask guest
    # ==========================================================
    @staticmethod
    def _purge_guest_session():
        """Nettoie toutes les variables Strava du guest côté session Flask."""
        for key in [
            "guest_id",
            "strava_token",
            "strava_refresh_token",
            "strava_expires_at",
            "selected_activity",
        ]:
            session.pop(key, None)

    # ==========================================================
    # 🧮 Recalcule global du compteur Strava (AppStats)
    # ==========================================================
    @staticmethod
    def recalculate_connected_count():
        """
        Recalcule le nombre total d'athlètes connectés à Strava :
        - Users avec strava_access_token actif
        - Guests avec session valide
        """
        from models.db_database import AppStats, User, GuestStravaSession

        now = time.time()

        # Comptage utilisateurs actifs
        users_connected = User.query.filter(
            User.strava_access_token.isnot(None),
            User.strava_access_token != "",
            User.strava_token_expires_at > now
        ).count()

        # Comptage invités actifs
        guests_connected = GuestStravaSession.query.filter(
            GuestStravaSession.strava_token_expires_at > now
        ).count()


        total = users_connected + guests_connected

        # Mise à jour AppStats
        stats = AppStats.query.first()
        if not stats:
            stats = AppStats(strava_connected_count=total)
            db.session.add(stats)
        else:
            stats.strava_connected_count = total

        db.session.commit()

        #print(f"🔄 AppStats.strava_connected_count mis à jour → {total} (users={users_connected}, guests={guests_connected})")
        return total

    @staticmethod
    def cleanup_expired_connections():
        """
        Supprime les connexions expirées (users + guests)
        et met à jour le compteur local AppStats.
        """
        from models.db_database import User, GuestStravaSession, AppStats

        now = time.time()
        cleaned = 0

        # 🧹 Supprime les users dont le token a expiré
        expired_users = User.query.filter(
            User.strava_token_expires_at.isnot(None),
            User.strava_token_expires_at <= now
        ).all()

        for u in expired_users:
            u.strava_access_token = None
            u.strava_refresh_token = None
            u.strava_token_expires_at = None
            cleaned += 1

        # 🧹 Supprime les guests expirés
        expired_guests = GuestStravaSession.query.filter(
            GuestStravaSession.strava_token_expires_at <= now
        ).all()

        for g in expired_guests:
            db.session.delete(g)
            cleaned += 1

        db.session.commit()

        # 🔁 Recalcul global
        total = StravaService.recalculate_connected_count()

        print(f"🧹 {cleaned} connexions expirées nettoyées. Nouveau total : {total}")
        return cleaned


    @staticmethod
    def exchange_code_for_token(code: str) -> dict | None:
        """Échange le code d'autorisation contre un token."""
        response = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": current_app.config["STRAVA_CLIENT_ID"],
            "client_secret": current_app.config["STRAVA_CLIENT_SECRET"],
            "code": code,
            "grant_type": "authorization_code",
        })

        if response.status_code != 200:
            return None

        return response.json()

    @staticmethod
    def fetch_activity(token: str, activity_id: str) -> dict | None:
        """
        Récupère les détails d'une activité Strava spécifique.
        """
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}",
            headers=headers
        )
        if response.status_code == 200:
            return response.json()
        else:
            print(f"⚠️ Erreur Strava fetch_activity: {response.status_code}")
            return None

    @staticmethod
    def fetch_all_activities(token: str, max_pages: int = 5, per_page: int = 100) -> list:
        """
        Récupère toutes les activités Strava d'un utilisateur (jusqu'à max_pages * per_page).
        """
        headers = {"Authorization": f"Bearer {token}"}
        all_activities = []

        for page in range(1, max_pages + 1):
            response = requests.get(
                "https://www.strava.com/api/v3/athlete/activities",
                headers=headers,
                params={"per_page": per_page, "page": page}
            )

            if response.status_code != 200:
                print(f"⚠️ Erreur Strava page {page} : {response.status_code}")
                break

            data = response.json()
            if not data:
                break  # Plus d’activités à charger

            all_activities.extend(data)

        print(f"✅ {len(all_activities)} activités récupérées depuis Strava.")
        return all_activities


    # ==========================================================
    # 🧩 Récupération du token Strava pour un order (user ou guest)
    # ==========================================================
    @staticmethod
    def get_token_for_order(order):
        """
        Retourne (user, token) pour un order :
        - tente d'abord le token user si user_id présent
        - sinon (ou si token user absent), tente un token guest encore valide
        """
        from models.db_database import User, GuestStravaSession
        import time

        if not order:
            print("⚠️ [get_token_for_order] Aucun order fourni.")
            return None, None

        # 1) Cas USER : si l'order a un user_id, on tente son token
        if getattr(order, "user_id", None):
            user = User.query.get(order.user_id)
            if user:
                token = StravaService.get_token(user)  # sans refresh automatique
                if token:
                    print(f"🟢 [get_token_for_order] Token Strava user trouvé pour {user.email}")
                    return user, token
                else:
                    print(f"🔴 [get_token_for_order] Aucun token actif pour user {user.email}")

        # 2) Cas GUEST : prendre la dernière session guest encore valide
        guest_session = GuestStravaSession.query.order_by(GuestStravaSession.id.desc()).first()
        if guest_session:
            token = getattr(guest_session, "strava_access_token", None)
            expires_at = getattr(guest_session, "strava_token_expires_at", None)
            if token and expires_at and expires_at > time.time():
                print(f"🟢 [get_token_for_order] Token guest actif trouvé pour guest_id={guest_session.guest_id}")
                return None, token

        print("🔴 [get_token_for_order] Aucun token Strava valide (ni user ni guest).")
        return None, None


    # ==========================================================
    # 🔄 Migration d'une session guest vers un compte user
    # ==========================================================
    @staticmethod
    def migrate_guest_to_user(user):
        """
        Si un utilisateur se connecte alors qu'il avait une session guest Strava active :
        - transfère le token Strava vers son compte user si valide
        - sinon, nettoie la session et décrémente le compteur
        """

        from models.db_database import GuestStravaSession
        from flask import session
        from services.strava_service import decrement_strava_connections
        import time

        guest_id = session.get("guest_id")
        guest_token = session.get("strava_token")
        guest_refresh = session.get("strava_refresh_token")
        guest_expires = session.get("strava_expires_at")

        # Aucun lien Strava guest à traiter
        if not guest_id and not guest_token:
            return

        # Récupère la session guest en base (si elle existe)
        guest_entry = GuestStravaSession.query.filter_by(guest_id=guest_id).first()

        # ⚙️ Cas 1 : guest Strava actif → on migre
        if guest_token and guest_expires and guest_expires > time.time():
            print(f"🔄 Migration du token Strava guest vers user {user.id}")

            user.strava_access_token = guest_token
            user.strava_refresh_token = guest_refresh
            user.strava_token_expires_at = guest_expires
            db.session.commit()

            # Supprime la guest session (mais pas de décrément)
            if guest_entry:
                db.session.delete(guest_entry)
                db.session.commit()

            # Nettoyage session Flask
            for key in [
                "guest_id", "strava_token", "strava_refresh_token",
                "strava_expires_at", "selected_activity",
            ]:
                session.pop(key, None)

            # 🚫 Ne décrémente pas le compteur ici
            # ✅ Empêche double incrément dans /authorized
            session["skip_strava_increment_once"] = True

            print(f"✅ Guest Strava migré vers user {user.id} sans perte de token.")

        # ⚙️ Cas 2 : token expiré ou invalide → nettoyage complet
        else:
            print("🧹 Token Strava guest expiré ou invalide, suppression complète.")

            # Supprime en base
            if guest_entry:
                db.session.delete(guest_entry)
                db.session.commit()

            # Nettoyage session
            for key in [
                "guest_id", "strava_token", "strava_refresh_token",
                "strava_expires_at", "selected_activity",
            ]:
                session.pop(key, None)

            # Décrémente le compteur (car le slot est libéré)
            decrement_strava_connections()
            StravaService.recalculate_connected_count()




    
def increment_strava_connections():
    """Incrémente le compteur local (users + guests) et recalcule pour cohérence."""
    from models.db_database import AppStats
    from services.strava_service import StravaService

    stats = AppStats.query.first()
    if not stats:
        stats = AppStats(strava_connected_count=0)
        db.session.add(stats)
    stats.strava_connected_count += 1
    db.session.commit()

    print(f"⬆️ Compteur local Strava incrémenté → {stats.strava_connected_count}")
    StravaService.recalculate_connected_count()


def decrement_strava_connections():
    """Décrémente le compteur local (users + guests) et recalcule pour cohérence."""
    from models.db_database import AppStats
    from services.strava_service import StravaService

    stats = AppStats.query.first()
    if stats and stats.strava_connected_count > 0:
        stats.strava_connected_count -= 1
        db.session.commit()
        print(f"⬇️ Compteur local Strava décrémenté → {stats.strava_connected_count}")
    else:
        print("⚠️ Aucun compteur à décrémenter ou déjà à 0.")

    StravaService.recalculate_connected_count()




