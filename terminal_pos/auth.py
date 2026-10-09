"""Autenticación local de operadores; separada del PIN ficticio de las tarjetas."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from .db import connect, operator_pin_hash, transaction, utc_now
from .service import PosError


SESSION_HOURS = 8
LOCK_MINUTES = 15
MAX_FAILURES = 5


def _digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def login(directory, code, pin):
    if not isinstance(code, str) or not code.isdecimal() or len(code) > 6 or not isinstance(pin, str) or not pin.isdecimal() or len(pin) != 6:
        raise PosError("INVALID_CREDENTIALS", "Código o PIN incorrecto", 401)
    now = datetime.now(timezone.utc)
    with connect(directory) as db, transaction(db):
        row = db.execute("SELECT * FROM operator WHERE id=? AND active=1", (int(code),)).fetchone()
        if row is None:
            raise PosError("INVALID_CREDENTIALS", "Código o PIN incorrecto", 401)
        if row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > now:
            raise PosError("LOGIN_LOCKED", "Acceso temporalmente bloqueado", 429)
        actual = operator_pin_hash(pin, row["pin_salt"]) if row["pin_salt"] and row["pin_digest"] else ""
        if not hmac.compare_digest(actual, row["pin_digest"] or ""):
            failures = (0 if row["locked_until"] else row["failed_logins"]) + 1
            locked_until = (now + timedelta(minutes=LOCK_MINUTES)).replace(microsecond=0).isoformat() if failures >= MAX_FAILURES else None
            db.execute("UPDATE operator SET failed_logins=?,locked_until=? WHERE id=?",
                       (failures, locked_until, row["id"]))
            if locked_until:
                return_error = PosError("LOGIN_LOCKED", "Acceso temporalmente bloqueado", 429)
            else:
                return_error = PosError("INVALID_CREDENTIALS", "Código o PIN incorrecto", 401)
        else:
            return_error = None
            db.execute("UPDATE operator SET failed_logins=0,locked_until=NULL WHERE id=?", (row["id"],))
            token = secrets.token_urlsafe(32)
            expires = (now + timedelta(hours=SESSION_HOURS)).replace(microsecond=0).isoformat()
            db.execute("INSERT INTO operator_session VALUES (?,?,?)", (_digest(token), row["id"], expires))
            result = {"id": row["id"], "name": row["name"], "token": token}
    if return_error:
        raise return_error
    return result


def current_operator(directory, token):
    if not token or len(token) > 200:
        raise PosError("AUTH_REQUIRED", "Iniciá sesión para usar el POS", 401)
    with connect(directory) as db:
        row = db.execute("""SELECT o.id,o.name FROM operator_session s
            JOIN operator o ON o.id=s.operator_id
            WHERE s.token_digest=? AND s.expires_at>? AND o.active=1""",
            (_digest(token), utc_now())).fetchone()
    if row is None:
        raise PosError("AUTH_REQUIRED", "Iniciá sesión para usar el POS", 401)
    return dict(row)


def logout(directory, token):
    if token and len(token) <= 200:
        with connect(directory) as db, transaction(db):
            db.execute("DELETE FROM operator_session WHERE token_digest=?", (_digest(token),))
    return {"ok": True}
