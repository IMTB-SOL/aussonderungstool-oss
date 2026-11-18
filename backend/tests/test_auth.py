"""Tests für die E-Mail/Passwort-Authentifizierung."""

import pytest
from models import Base, User, engine
from sqlalchemy.orm import Session


@pytest.fixture
def db():
    """Frische Tabellen + Session auf der Test-SQLite (conftest lenkt DATABASE_URL um)."""
    Base.metadata.create_all(bind=engine)
    sitzung = Session(engine)
    # Nutzer-Tabelle vor jedem Test leeren, damit Tests unabhängig sind.
    sitzung.query(User).delete()
    sitzung.commit()
    try:
        yield sitzung
    finally:
        sitzung.close()


def test_user_anlegen_und_lesen(db):
    u = User(
        email="anna@example.com",
        password_hash="deadbeef",
        salt="cafe",
        name="anna",
        approved=False,
        is_admin=False,
    )
    db.add(u)
    db.commit()
    geladen = db.query(User).filter_by(email="anna@example.com").first()
    assert geladen is not None
    assert geladen.id > 0
    assert geladen.approved is False
    assert geladen.is_admin is False
    assert geladen.erstellt_am is not None


def test_email_ist_unique(db):
    from sqlalchemy.exc import IntegrityError

    db.add(User(email="dup@example.com", password_hash="a", salt="b", name="d"))
    db.commit()
    db.add(User(email="dup@example.com", password_hash="c", salt="d", name="e"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --- Auth-Kern -------------------------------------------------------------

from utils import auth


def test_hash_roundtrip_und_konstantzeit():
    h, s = auth.hash_passwort("geheim123456")
    assert h and s and h != s
    assert auth.pruefe_passwort("geheim123456", h, s) is True
    assert auth.pruefe_passwort("falsch", h, s) is False


def test_hash_hat_pro_user_unterschiedliches_salt():
    h1, s1 = auth.hash_passwort("gleichespasswort")
    h2, s2 = auth.hash_passwort("gleichespasswort")
    assert s1 != s2
    assert h1 != h2  # unterschiedliches Salt → unterschiedlicher Hash


def test_registriere_user_legt_unbestaetigten_user_an(db):
    u = auth.registriere_user(db, "neu@example.com", "passwort1234")
    assert u.id > 0
    assert u.approved is False
    assert u.is_admin is False
    assert u.email == "neu@example.com"
    assert auth.pruefe_passwort("passwort1234", u.password_hash, u.salt)


def test_registriere_user_duplikat_wirft(db):
    auth.registriere_user(db, "d@example.com", "passwort1234")
    with pytest.raises(ValueError):
        auth.registriere_user(db, "d@example.com", "anderespw123")


def test_verifiziere_login(db):
    auth.registriere_user(db, "log@example.com", "meinpasswort1")
    assert auth.verifiziere_login(db, "log@example.com", "falsch") is None
    u = auth.verifiziere_login(db, "log@example.com", "meinpasswort1")
    assert u is not None and u.email == "log@example.com"


def test_session_cookie_roundtrip():
    cookie = auth.erzeuge_session_cookie({"id": 42})
    assert auth.pruefe_session(cookie) == {"id": 42}
    assert auth.pruefe_session("kaputt") is None


def test_ermittle_auth_modus(monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    assert auth.ermittle_auth_modus() == "passwort"
    monkeypatch.setenv("AUTH_MODUS", "disabled")
    assert auth.ermittle_auth_modus() == "disabled"
    monkeypatch.setenv("AUTH_MODUS", "quatsch")
    with pytest.raises(EnvironmentError):
        auth.ermittle_auth_modus()
    monkeypatch.delenv("AUTH_MODUS", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    assert auth.ermittle_auth_modus() == "passwort"
    # Sicherer Default: auch außerhalb der Produktion bleibt die Anmeldung an.
    monkeypatch.setenv("APP_ENV", "development")
    assert auth.ermittle_auth_modus() == "passwort"


# --- Endpoints & Middleware ------------------------------------------------

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from models import get_db
from routers.auth import AuthMiddleware
from routers.auth import router as auth_router


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Isoliert den In-Memory-Rate-Limit-Store zwischen Tests.

    Der Store lebt als Modul-Global in routers.auth und würde sonst über alle
    Tests dieser Datei hinweg mitzählen (TestClient meldet für jede Anfrage
    dieselbe Client-IP) — ohne Reset liefen spätere Tests in die Limits der
    vorherigen und schlügen mit 429 statt des erwarteten Status fehl.
    """
    from routers import auth as auth_router_mod

    auth_router_mod._rate_store.clear()
    yield
    auth_router_mod._rate_store.clear()


@pytest.fixture
def app_client(db):
    """App nur mit Auth-Router (Registrierung/Login/Logout/Me)."""
    app = FastAPI()
    app.include_router(auth_router)
    return TestClient(app)


def _reg(client, email="user@example.com", pw="passwort1234"):
    return client.post("/api/auth/register", json={"email": email, "passwort": pw})


def test_register_gibt_pending(app_client, monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    r = _reg(app_client)
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_register_duplikat_409(app_client, monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    _reg(app_client)
    r = _reg(app_client)
    assert r.status_code == 409


def test_register_ungueltige_email_400(app_client, monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    r = app_client.post(
        "/api/auth/register", json={"email": "keine-mail", "passwort": "passwort1234"}
    )
    assert r.status_code == 400


def test_register_zu_kurzes_passwort_400(app_client, monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    monkeypatch.setenv("AUTH_PASSWORT_MIN", "12")
    r = app_client.post("/api/auth/register", json={"email": "x@example.com", "passwort": "kurz"})
    assert r.status_code == 400


def test_login_vor_freischaltung_403(app_client, monkeypatch, db):
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    _reg(app_client, "warte@example.com")
    r = app_client.post(
        "/api/auth/login", json={"email": "warte@example.com", "passwort": "passwort1234"}
    )
    assert r.status_code == 403


def test_login_nach_freischaltung_setzt_cookie(app_client, monkeypatch, db):
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    _reg(app_client, "ok@example.com")
    user = db.query(User).filter_by(email="ok@example.com").first()
    user.approved = True
    db.commit()
    r = app_client.post(
        "/api/auth/login", json={"email": "ok@example.com", "passwort": "passwort1234"}
    )
    assert r.status_code == 200
    assert app_client.cookies.get("aussonderung_session")
    assert r.json()["user"]["email"] == "ok@example.com"


def test_login_gesperrt_wenn_nur_azure_erlaubt(app_client, monkeypatch, db):
    """Bei AUTH_MODUS=azure darf sich auch ein bestehendes Passwort-Konto nicht anmelden."""
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    _reg(app_client, "alt@example.com")
    user = db.query(User).filter_by(email="alt@example.com").first()
    user.approved = True
    db.commit()

    monkeypatch.setenv("AUTH_MODUS", "azure")
    monkeypatch.setattr(auth, "ist_azure_konfiguriert", lambda: True)
    r = app_client.post(
        "/api/auth/login", json={"email": "alt@example.com", "passwort": "passwort1234"}
    )
    assert r.status_code == 403
    assert not app_client.cookies.get("aussonderung_session")


def test_login_falsches_passwort_401(app_client, monkeypatch, db):
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    _reg(app_client, "pw@example.com")
    user = db.query(User).filter_by(email="pw@example.com").first()
    user.approved = True
    db.commit()
    r = app_client.post(
        "/api/auth/login", json={"email": "pw@example.com", "passwort": "falschfalsch"}
    )
    assert r.status_code == 401


def test_me_nach_login(app_client, monkeypatch, db):
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    _reg(app_client, "me@example.com")
    user = db.query(User).filter_by(email="me@example.com").first()
    user.approved = True
    db.commit()
    app_client.post("/api/auth/login", json={"email": "me@example.com", "passwort": "passwort1234"})
    r = app_client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "me@example.com"
    assert r.json()["auth_aktiv"] is True


def test_middleware_schuetzt_route(db, monkeypatch):
    """Frische App mit Middleware + Dummy-Route: ohne Cookie 401, mit gültiger Session 200."""
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    from utils import auth as auth_mod

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/geschuetzt")
    async def geschuetzt(request: Request):
        return {"user": request.state.user}

    client = TestClient(app)
    # Ohne Cookie → 401
    assert client.get("/api/geschuetzt").status_code == 401
    # Freigeschalteten User anlegen + gültiges Cookie setzen
    u = auth_mod.registriere_user(db, "mw@example.com", "passwort1234")
    u.approved = True
    db.commit()
    cookie = auth_mod.erzeuge_session_cookie({"id": u.id})
    client.cookies.set("aussonderung_session", cookie)
    r = client.get("/api/geschuetzt")
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "mw@example.com"
    assert r.json()["user"]["is_admin"] is False


# --- Admin-Autorisierung & Nutzerverwaltung --------------------------------

from dependencies import require_admin


def test_require_admin_blockt_nicht_admin(monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "passwort")

    class _Req:
        class state:
            user = {"id": 1, "name": "x", "email": "x@e.de", "is_admin": False}

    with pytest.raises(HTTPException) as exc:
        require_admin(_Req())
    assert exc.value.status_code == 403


def test_require_admin_laesst_admin_durch(monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "passwort")

    class _Req:
        class state:
            user = {"id": 1, "name": "a", "email": "a@e.de", "is_admin": True}

    assert require_admin(_Req())["is_admin"] is True


def test_require_admin_disabled_ist_dev_admin(monkeypatch):
    monkeypatch.setenv("AUTH_MODUS", "disabled")

    class _Req:
        class state:
            pass

    assert require_admin(_Req())["is_admin"] is True


@pytest.fixture
def admin_client(db, monkeypatch):
    """App mit Admin-Router im disabled-Modus (require_admin = Dev-Admin)."""
    monkeypatch.setenv("AUTH_MODUS", "disabled")
    from routers.admin import router as admin_router

    app = FastAPI()
    app.include_router(admin_router)
    return TestClient(app)


def test_nutzerverwaltung_liste_freischalten_rolle_loeschen(admin_client, db):
    from utils import auth as auth_mod

    u = auth_mod.registriere_user(db, "verwalte@example.com", "passwort1234")
    uid = u.id
    # Liste
    r = admin_client.get("/api/admin/users")
    assert r.status_code == 200
    assert any(x["email"] == "verwalte@example.com" for x in r.json()["users"])
    # Freischalten
    r = admin_client.put(f"/api/admin/users/{uid}/freischalten")
    assert r.status_code == 200
    db.expire_all()
    assert db.get(User, uid).approved is True
    # Admin-Rolle setzen
    r = admin_client.put(f"/api/admin/users/{uid}/rolle", json={"is_admin": True})
    assert r.status_code == 200
    db.expire_all()
    assert db.get(User, uid).is_admin is True
    # Zweiten Admin anlegen, damit der "letzter Admin"-Schutz beim Löschen
    # unten nicht greift (uid ist sonst der einzige DB-Admin).
    zweiter = auth_mod.registriere_user(db, "zweiter-admin@example.com", "passwort1234")
    zweiter.is_admin = True
    zweiter.approved = True
    db.commit()
    # Löschen
    r = admin_client.delete(f"/api/admin/users/{uid}")
    assert r.status_code == 200
    db.expire_all()
    assert db.get(User, uid) is None


# --- Erst-Admin-Seed -------------------------------------------------------

from utils.seed_admin import seede_admin


def test_seede_admin_legt_admin_an(db, monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "chef@example.com")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "starkespasswort1")
    seede_admin(db)
    admin = db.query(User).filter_by(email="chef@example.com").first()
    assert admin is not None
    assert admin.is_admin is True
    assert admin.approved is True
    # Idempotent: zweiter Lauf legt keinen zweiten Admin an
    seede_admin(db)
    assert db.query(User).filter_by(is_admin=True).count() == 1


def test_seede_admin_ohne_env_macht_nichts(db, monkeypatch):
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    seede_admin(db)
    assert db.query(User).count() == 0


def test_letzter_admin_nicht_loeschbar(admin_client, db):
    from utils import auth as auth_mod

    a = auth_mod.registriere_user(db, "einziger@example.com", "passwort1234")
    a.is_admin = True
    a.approved = True
    db.commit()
    r = admin_client.delete(f"/api/admin/users/{a.id}")
    assert r.status_code == 400
    db.expire_all()
    assert db.get(User, a.id) is not None  # nicht gelöscht


def test_letzter_admin_nicht_degradierbar(admin_client, db):
    from utils import auth as auth_mod

    a = auth_mod.registriere_user(db, "einzig2@example.com", "passwort1234")
    a.is_admin = True
    a.approved = True
    db.commit()
    r = admin_client.put(f"/api/admin/users/{a.id}/rolle", json={"is_admin": False})
    assert r.status_code == 400
    db.expire_all()
    assert db.get(User, a.id).is_admin is True


def test_seede_admin_erhebt_bestehenden_user(db, monkeypatch):
    from utils import auth as auth_mod
    from utils.seed_admin import seede_admin

    u = auth_mod.registriere_user(db, "promote@example.com", "passwort1234")
    assert u.is_admin is False
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "promote@example.com")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "egal12345678")
    seede_admin(db)
    db.expire_all()
    promoted = db.query(User).filter_by(email="promote@example.com").first()
    assert promoted.is_admin is True
    assert promoted.approved is True


def test_seede_admin_passwort_authentifiziert(db, monkeypatch):
    from utils import auth as auth_mod
    from utils.seed_admin import seede_admin

    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "seedadmin@example.com")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "seedpasswort123")
    seede_admin(db)
    u = auth_mod.verifiziere_login(db, "seedadmin@example.com", "seedpasswort123")
    assert u is not None
    assert u.is_admin is True


# --- Dual-Auth: Router -----------------------------------------------------


def test_auth_config_spiegelt_modus(app_client, monkeypatch):
    from utils import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ist_azure_konfiguriert", lambda: True)
    monkeypatch.setenv("AUTH_MODUS", "beide")
    r = app_client.get("/api/auth/config")
    assert r.status_code == 200
    assert r.json() == {"azure_aktiv": True, "passwort_aktiv": True}
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    assert app_client.get("/api/auth/config").json() == {
        "azure_aktiv": False,
        "passwort_aktiv": True,
    }


def test_azure_login_ohne_config_501(app_client, monkeypatch):
    monkeypatch.setattr("routers.auth.ist_azure_konfiguriert", lambda: False)
    r = app_client.get("/api/auth/azure/login", follow_redirects=False)
    assert r.status_code == 501


def test_azure_callback_ohne_state_400(app_client, monkeypatch):
    monkeypatch.setattr("routers.auth.ist_azure_konfiguriert", lambda: True)
    r = app_client.get("/api/auth/callback", follow_redirects=False)
    assert r.status_code == 400


def test_azure_callback_happy_path_setzt_cookie_und_upsertet(app_client, monkeypatch, db):
    monkeypatch.setattr("routers.auth.ist_azure_konfiguriert", lambda: True)
    monkeypatch.setattr(
        "routers.auth.verarbeite_callback",
        lambda auth_response, state, redirect_uri: {
            "email": "cb@example.com",
            "name": "CB",
            "oid": "o1",
        },
    )
    app_client.cookies.set("auth_state", "irgendein-state")
    r = app_client.get("/api/auth/callback?code=x", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].endswith("/app.html")
    assert "aussonderung_session" in r.cookies
    from models import User

    db.expire_all()
    u = db.query(User).filter_by(email="cb@example.com").first()
    assert u is not None
    assert u.provider == "azure"
    assert u.approved is True
    assert not u.password_hash


def test_me_enthaelt_provider(app_client, monkeypatch, db):
    from utils import auth as auth_mod

    monkeypatch.setenv("AUTH_MODUS", "passwort")
    u = auth_mod.registriere_user(db, "prov@example.com", "passwort1234")
    u.approved = True
    db.commit()
    app_client.post(
        "/api/auth/login", json={"email": "prov@example.com", "passwort": "passwort1234"}
    )
    me = app_client.get("/api/auth/me").json()
    assert me["provider"] == "passwort"


def test_middleware_akzeptiert_azure_session(db, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from routers.auth import AuthMiddleware
    from utils import auth as auth_mod

    monkeypatch.setenv("AUTH_MODUS", "beide")
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/geschuetzt")
    async def geschuetzt(request: Request):
        return {"user": request.state.user}

    client = TestClient(app)
    u = auth_mod.upsert_azure_user(db, {"email": "sess@example.com", "name": "Sess"})
    client.cookies.set("aussonderung_session", auth_mod.erzeuge_session_cookie({"id": u.id}))
    r = client.get("/api/geschuetzt")
    assert r.status_code == 200
    assert r.json()["user"]["provider"] == "azure"
    assert r.json()["user"]["is_admin"] is False


# --- Dual-Auth: provider + nullable Passwörter -----------------------------


def test_azure_user_ohne_passwort_anlegbar(db):
    u = User(email="ms@example.com", name="ms", approved=True, is_admin=False, provider="azure")
    db.add(u)
    db.commit()
    geladen = db.query(User).filter_by(email="ms@example.com").first()
    assert geladen.provider == "azure"
    assert geladen.password_hash is None
    assert geladen.salt is None


def test_passwort_user_provider_default(db):
    from utils import auth as auth_mod

    u = auth_mod.registriere_user(db, "lokal@example.com", "passwort1234")
    assert u.provider == "passwort"


# --- Dual-Auth: Auth-Kern --------------------------------------------------


def test_pruefe_passwort_ohne_hash_ist_false():
    from utils import auth as auth_mod

    assert auth_mod.pruefe_passwort("egal", None, None) is False
    assert auth_mod.pruefe_passwort("egal", "", "") is False


def test_azure_konto_nicht_per_passwort_anmeldbar(db):
    from utils import auth as auth_mod

    auth_mod.upsert_azure_user(db, {"email": "azure@example.com", "name": "Azure Nutzer"})
    assert auth_mod.verifiziere_login(db, "azure@example.com", "irgendwas") is None


def test_upsert_azure_user_neu_und_bestehend(db):
    from utils import auth as auth_mod

    u1 = auth_mod.upsert_azure_user(db, {"email": "A@Example.com", "name": "A"})
    assert u1.provider == "azure" and u1.approved is True and not u1.password_hash
    # gleiche E-Mail (case-insensitiv) → dieselbe Zeile, kein Duplikat
    u2 = auth_mod.upsert_azure_user(db, {"email": "a@example.com", "name": "A neu"})
    assert u2.id == u1.id
    assert db.query(User).filter_by(email="a@example.com").count() == 1


def test_ermittle_auth_modus_dual(monkeypatch):
    from utils import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ist_azure_konfiguriert", lambda: True)
    monkeypatch.setenv("AUTH_MODUS", "beide")
    assert auth_mod.ermittle_auth_modus() == "beide"
    assert auth_mod.azure_aktiv() is True
    assert auth_mod.passwort_aktiv() is True
    monkeypatch.setenv("AUTH_MODUS", "azure")
    assert auth_mod.ermittle_auth_modus() == "azure"
    assert auth_mod.passwort_aktiv() is False
    monkeypatch.setenv("AUTH_MODUS", "passwort")
    assert auth_mod.azure_aktiv() is False


def test_ermittle_auth_modus_azure_ohne_config_wirft(monkeypatch):
    from utils import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ist_azure_konfiguriert", lambda: False)
    monkeypatch.setenv("AUTH_MODUS", "azure")
    with pytest.raises(EnvironmentError):
        auth_mod.ermittle_auth_modus()


def test_ermittle_auth_modus_beide_ohne_config_faellt_auf_passwort(monkeypatch):
    from utils import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ist_azure_konfiguriert", lambda: False)
    monkeypatch.setenv("AUTH_MODUS", "beide")
    assert auth_mod.ermittle_auth_modus() == "passwort"


def test_upsert_azure_pending_konto_wird_azure_und_passwort_entwertet(db):
    from utils import auth as auth_mod

    u = auth_mod.registriere_user(db, "pending@example.com", "attackerpw12345")
    assert u.approved is False
    same = auth_mod.upsert_azure_user(db, {"email": "pending@example.com", "name": "P"})
    assert same.id == u.id
    assert same.approved is True
    assert same.provider == "azure"
    # Das vorab (evtl. von einem Angreifer) gesetzte Passwort ist entwertet:
    assert not same.password_hash
    assert auth_mod.verifiziere_login(db, "pending@example.com", "attackerpw12345") is None


def test_upsert_azure_laesst_freigeschaltetes_lokales_konto_unberuehrt(db):
    from utils import auth as auth_mod

    u = auth_mod.registriere_user(db, "lokal-ok@example.com", "meinpasswort123")
    u.approved = True
    db.commit()
    auth_mod.upsert_azure_user(db, {"email": "lokal-ok@example.com", "name": "L"})
    db.expire_all()
    same = db.query(User).filter_by(email="lokal-ok@example.com").first()
    assert same.provider == "passwort"
    assert auth_mod.verifiziere_login(db, "lokal-ok@example.com", "meinpasswort123") is not None


def test_upsert_azure_neuer_user_hat_leeres_passwort(db):
    from utils import auth as auth_mod

    u = auth_mod.upsert_azure_user(db, {"email": "neu-azure@example.com", "name": "N"})
    assert u.provider == "azure"
    assert u.approved is True
    assert u.password_hash == ""
    assert auth_mod.verifiziere_login(db, "neu-azure@example.com", "irgendwas") is None


def test_register_gesperrt_wenn_passwort_inaktiv(db, monkeypatch):
    from utils import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ist_azure_konfiguriert", lambda: True)
    monkeypatch.setenv("AUTH_MODUS", "azure")
    app = FastAPI()
    app.include_router(auth_router)
    client = TestClient(app)
    r = client.post(
        "/api/auth/register", json={"email": "x@example.com", "passwort": "passwort12345"}
    )
    assert r.status_code == 403
