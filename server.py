"""POS de cobranzas educativo. Prestadoras, facturas y confirmaciones son simuladas."""

from __future__ import annotations

import json
import base64
import hashlib
import hmac
import html
import io
import argparse
import secrets
import socket
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from time import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import segno

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LOCK = threading.RLock()
SESSIONS = {}
SESSION_SECONDS = 8 * 60 * 60
DEMO_PINS = {1: "1234", 2: "5678"}
QR_SECONDS = 5 * 60
MOBILE_BASE = None
PROVIDERS = {
    "ANDE": {"name": "ANDE", "reference_label": "NIS"},
    "ESSAP": {"name": "ESSAP", "reference_label": "ISSAN"},
    "TIGO": {"name": "Tigo Hogar", "reference_label": "Documento del titular (demo)"},
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    DATA.mkdir(exist_ok=True)
    db = sqlite3.connect(DATA / "pos.db", timeout=10)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("ATTACH DATABASE ? AS prestadora", (str(DATA / "prestadoras_simuladas.db"),))
        with db:
            yield db
    finally:
        db.close()


def init_db():
    with LOCK, connect() as db:
        # ATTACH mantiene atómicos los cambios entre archivos si no se usa WAL.
        db.execute("PRAGMA main.journal_mode=DELETE")
        db.execute("PRAGMA prestadora.journal_mode=DELETE")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS empleado (
                id INTEGER PRIMARY KEY, nombre TEXT NOT NULL,
                pin_salt BLOB, pin_hash BLOB
            );
            CREATE TABLE IF NOT EXISTS caja (
                id INTEGER PRIMARY KEY, empleado_id INTEGER NOT NULL REFERENCES empleado(id),
                abierta_en TEXT NOT NULL, cerrada_en TEXT
            );
            CREATE TABLE IF NOT EXISTS medio_pago (
                codigo TEXT PRIMARY KEY, nombre TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cobro (
                id TEXT PRIMARY KEY, solicitud_id TEXT NOT NULL UNIQUE,
                caja_id INTEGER NOT NULL REFERENCES caja(id),
                prestadora TEXT NOT NULL, referencia TEXT NOT NULL,
                titular TEXT NOT NULL, importe INTEGER NOT NULL CHECK(importe > 0),
                medio TEXT NOT NULL REFERENCES medio_pago(codigo),
                confirmado_en TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS aplicacion (
                cobro_id TEXT NOT NULL REFERENCES cobro(id),
                factura_id TEXT NOT NULL, importe INTEGER NOT NULL CHECK(importe > 0),
                PRIMARY KEY(cobro_id, factura_id)
            );
            CREATE TABLE IF NOT EXISTS qr_solicitud (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                caja_id INTEGER NOT NULL REFERENCES caja(id),
                prestadora TEXT NOT NULL, referencia TEXT NOT NULL,
                facturas_json TEXT NOT NULL, importe INTEGER NOT NULL,
                expira_en INTEGER NOT NULL,
                estado TEXT NOT NULL CHECK(estado IN ('PENDIENTE','CONFIRMADA','CANCELADA')),
                cobro_id TEXT REFERENCES cobro(id)
            );
            CREATE TABLE IF NOT EXISTS prestadora.cuenta (
                prestadora TEXT NOT NULL, referencia TEXT NOT NULL,
                titular TEXT NOT NULL, PRIMARY KEY(prestadora, referencia)
            );
            CREATE TABLE IF NOT EXISTS prestadora.factura (
                id TEXT PRIMARY KEY, prestadora TEXT NOT NULL,
                referencia TEXT NOT NULL, periodo TEXT NOT NULL,
                vencimiento TEXT NOT NULL, importe INTEGER NOT NULL CHECK(importe > 0),
                estado TEXT NOT NULL CHECK(estado IN ('PENDIENTE','PAGADA')),
                FOREIGN KEY(prestadora, referencia) REFERENCES cuenta(prestadora, referencia)
            );
            CREATE TABLE IF NOT EXISTS prestadora.pago (
                id TEXT PRIMARY KEY, solicitud_id TEXT UNIQUE,
                prestadora TEXT NOT NULL, referencia TEXT NOT NULL,
                origen TEXT NOT NULL, importe INTEGER NOT NULL,
                pagado_en TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prestadora.pago_factura (
                pago_id TEXT NOT NULL, factura_id TEXT NOT NULL,
                importe INTEGER NOT NULL, PRIMARY KEY(pago_id, factura_id)
            );
        """)
        db.executemany("INSERT OR IGNORE INTO medio_pago VALUES (?,?)", [
            ("EFECTIVO", "Efectivo"), ("TARJETA", "Tarjeta (simulada)"),
            ("QR", "QR (simulado)"),
        ])
        old_schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='cobro'").fetchone()[0]
        if "CHECK(medio = 'EFECTIVO')" in old_schema:
            db.commit()
            db.execute("PRAGMA foreign_keys=OFF")
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE cobro_nuevo (
                    id TEXT PRIMARY KEY, solicitud_id TEXT NOT NULL UNIQUE,
                    caja_id INTEGER NOT NULL REFERENCES caja(id),
                    prestadora TEXT NOT NULL, referencia TEXT NOT NULL,
                    titular TEXT NOT NULL, importe INTEGER NOT NULL CHECK(importe > 0),
                    medio TEXT NOT NULL REFERENCES medio_pago(codigo),
                    confirmado_en TEXT NOT NULL
                )""")
                db.execute("INSERT INTO cobro_nuevo SELECT * FROM cobro")
                db.execute("DROP TABLE cobro")
                db.execute("ALTER TABLE cobro_nuevo RENAME TO cobro")
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.execute("PRAGMA foreign_keys=ON")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("La migración de cobros dejó referencias inválidas")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(empleado)")}
        if "pin_salt" not in columns:
            db.execute("ALTER TABLE empleado ADD COLUMN pin_salt BLOB")
        if "pin_hash" not in columns:
            db.execute("ALTER TABLE empleado ADD COLUMN pin_hash BLOB")
        if db.execute("SELECT COUNT(*) FROM empleado").fetchone()[0] == 0:
            db.executemany("INSERT INTO empleado(id,nombre) VALUES (?,?)", [(1, "Lucía Benítez"), (2, "Diego Rojas")])
        for employee_id, pin in DEMO_PINS.items():
            row = db.execute("SELECT pin_hash FROM empleado WHERE id=?", (employee_id,)).fetchone()
            if row and row["pin_hash"] is None:
                salt = secrets.token_bytes(16)
                digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000)
                db.execute("UPDATE empleado SET pin_salt=?,pin_hash=? WHERE id=?", (salt, digest, employee_id))
        if db.execute("SELECT COUNT(*) FROM prestadora.cuenta").fetchone()[0] == 0:
            db.executemany("INSERT INTO prestadora.cuenta VALUES (?,?,?)", [
                ("ANDE", "DEMO-ANDE-001", "María González"),
                ("ANDE", "DEMO-ANDE-000", "Ana Duarte"),
                ("ESSAP", "DEMO-ESSAP-001", "María González"),
                ("TIGO", "DEMO-TIGO-001", "Carlos Medina"),
            ])
            db.executemany("INSERT INTO prestadora.factura VALUES (?,?,?,?,?,?,?)", [
                ("A-2026-09", "ANDE", "DEMO-ANDE-001", "09/2026", "2026-10-15", 128000, "PENDIENTE"),
                ("A-2026-08", "ANDE", "DEMO-ANDE-001", "08/2026", "2026-09-15", 119000, "PENDIENTE"),
                ("A-2026-07", "ANDE", "DEMO-ANDE-001", "07/2026", "2026-08-15", 117000, "PAGADA"),
                ("A-000-09", "ANDE", "DEMO-ANDE-000", "09/2026", "2026-10-15", 83000, "PAGADA"),
                ("E-2026-09", "ESSAP", "DEMO-ESSAP-001", "09/2026", "2026-10-20", 64000, "PENDIENTE"),
                ("T-2026-09", "TIGO", "DEMO-TIGO-001", "09/2026", "2026-10-18", 165000, "PENDIENTE"),
            ])
            db.executemany("INSERT INTO prestadora.pago VALUES (?,?,?,?,?,?,?)", [
                ("EXT-A-001", None, "ANDE", "DEMO-ANDE-001", "OTRO_CANAL", 117000, "2026-08-12T14:00:00+00:00"),
                ("EXT-A-000", None, "ANDE", "DEMO-ANDE-000", "OTRO_CANAL", 83000, "2026-09-17T14:00:00+00:00"),
            ])
            db.executemany("INSERT INTO prestadora.pago_factura VALUES (?,?,?)", [
                ("EXT-A-001", "A-2026-07", 117000),
                ("EXT-A-000", "A-000-09", 83000),
            ])


class BusinessError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def rowdict(row):
    return dict(row) if row else None


def require_account(db, provider, reference):
    if provider not in PROVIDERS:
        raise BusinessError("Prestadora inválida.")
    reference = str(reference or "").strip().upper()
    if not reference or len(reference) > 50:
        raise BusinessError("Ingresá una referencia válida.")
    account = db.execute(
        "SELECT * FROM prestadora.cuenta WHERE prestadora=? AND referencia=?",
        (provider, reference),
    ).fetchone()
    if not account:
        raise BusinessError("La referencia no existe en la prestadora simulada.", 404)
    return rowdict(account)


def lookup(provider, reference):
    with connect() as db:
        account = require_account(db, provider, reference)
        invoices = [rowdict(r) for r in db.execute(
            "SELECT id,periodo,vencimiento,importe,estado FROM prestadora.factura "
            "WHERE prestadora=? AND referencia=? AND estado='PENDIENTE' ORDER BY vencimiento,id",
            (provider, account["referencia"]),
        )]
        return {"account": account, "invoices": invoices,
                "status": "PENDIENTE" if invoices else "SIN_DEUDA"}


def employees():
    with connect() as db:
        return [rowdict(r) for r in db.execute("SELECT id,nombre FROM empleado ORDER BY id")]


def login(employee_id, pin):
    if type(employee_id) is not int or not isinstance(pin, str):
        raise BusinessError("Empleado o PIN incorrecto.", 401)
    with connect() as db:
        employee = db.execute("SELECT id,nombre,pin_salt,pin_hash FROM empleado WHERE id=?", (employee_id,)).fetchone()
    if not employee or employee["pin_hash"] is None:
        raise BusinessError("Empleado o PIN incorrecto.", 401)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), employee["pin_salt"], 200_000)
    if not hmac.compare_digest(digest, employee["pin_hash"]):
        raise BusinessError("Empleado o PIN incorrecto.", 401)
    token = secrets.token_urlsafe(32)
    with LOCK:
        SESSIONS[hashlib.sha256(token.encode()).hexdigest()] = (employee_id, time() + SESSION_SECONDS)
    return token, {"id": employee["id"], "nombre": employee["nombre"]}


def session_employee(token):
    if not token:
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    with LOCK:
        session = SESSIONS.get(key)
        if not session:
            return None
        if session[1] < time():
            SESSIONS.pop(key, None)
            return None
    with connect() as db:
        return rowdict(db.execute("SELECT id,nombre FROM empleado WHERE id=?", (session[0],)).fetchone())


def logout(token, employee_id):
    cash = current_cash()
    if cash and cash["empleado_id"] == employee_id:
        raise BusinessError("Cerrá tu caja antes de salir.", 409)
    with LOCK:
        SESSIONS.pop(hashlib.sha256(token.encode()).hexdigest(), None)


def current_cash():
    with connect() as db:
        return rowdict(db.execute(
            "SELECT caja.id,caja.empleado_id,empleado.nombre AS empleado,caja.abierta_en "
            "FROM caja JOIN empleado ON empleado.id=caja.empleado_id "
            "WHERE caja.cerrada_en IS NULL ORDER BY caja.id DESC LIMIT 1"
        ).fetchone())


def open_cash(employee_id):
    if type(employee_id) is not int:
        raise BusinessError("Empleado inválido.")
    with LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM caja WHERE cerrada_en IS NULL").fetchone():
            raise BusinessError("Ya hay una caja abierta.", 409)
        if not db.execute("SELECT 1 FROM empleado WHERE id=?", (employee_id,)).fetchone():
            raise BusinessError("Empleado inválido.")
        db.execute("INSERT INTO caja(empleado_id,abierta_en) VALUES (?,?)", (employee_id, now()))
        return rowdict(db.execute(
            "SELECT caja.id,caja.empleado_id,empleado.nombre AS empleado,caja.abierta_en "
            "FROM caja JOIN empleado ON empleado.id=caja.empleado_id WHERE caja.cerrada_en IS NULL"
        ).fetchone())


def close_cash(employee_id=None):
    with LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        cash = db.execute("SELECT id FROM caja WHERE cerrada_en IS NULL").fetchone()
        if not cash:
            raise BusinessError("No hay caja abierta.", 409)
        if employee_id is not None and db.execute("SELECT empleado_id FROM caja WHERE id=?", (cash["id"],)).fetchone()[0] != employee_id:
            raise BusinessError("Esta caja pertenece a otro empleado.", 403)
        total, count = db.execute(
            "SELECT COALESCE(SUM(importe),0),COUNT(*) FROM cobro WHERE caja_id=?", (cash["id"],)
        ).fetchone()
        by_method = {r["medio"]: r["total"] for r in db.execute(
            "SELECT medio,SUM(importe) AS total FROM cobro WHERE caja_id=? GROUP BY medio", (cash["id"],)
        )}
        db.execute("""UPDATE qr_solicitud SET estado='CONFIRMADA',
                   cobro_id=(SELECT id FROM cobro WHERE solicitud_id=qr_solicitud.id)
                   WHERE caja_id=? AND estado='PENDIENTE'
                   AND EXISTS(SELECT 1 FROM cobro WHERE solicitud_id=qr_solicitud.id)""", (cash["id"],))
        db.execute("UPDATE qr_solicitud SET estado='CANCELADA' WHERE caja_id=? AND estado='PENDIENTE'", (cash["id"],))
        db.execute("UPDATE caja SET cerrada_en=? WHERE id=?", (now(), cash["id"]))
        return {"caja_id": cash["id"], "cobros": count, "total": total, "by_method": by_method}


def charge(payload, employee_id=None, allow_qr=False):
    provider = payload.get("provider")
    reference = str(payload.get("reference") or "").strip().upper()
    request_id = str(payload.get("request_id") or "").strip()
    ids = payload.get("invoice_ids")
    medio = payload.get("medio", "EFECTIVO")
    if not request_id or len(request_id) > 80:
        raise BusinessError("Identificador de solicitud inválido.")
    if not isinstance(ids, list) or not ids or len(ids) > 30 or not all(isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
        raise BusinessError("Seleccioná facturas válidas sin repetir.")
    if medio not in ("EFECTIVO", "TARJETA", "QR"):
        raise BusinessError("Medio de pago inválido.")
    if medio == "QR" and not allow_qr:
        raise BusinessError("El QR debe confirmarse desde el celular.", 403)
    with LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT * FROM cobro WHERE solicitud_id=?", (request_id,)).fetchone()
        if previous:
            if employee_id is not None and db.execute(
                "SELECT empleado_id FROM caja WHERE id=?", (previous["caja_id"],)
            ).fetchone()[0] != employee_id:
                raise BusinessError("Este cobro pertenece a otro empleado.", 403)
            previous_ids = {r["factura_id"] for r in db.execute(
                "SELECT factura_id FROM aplicacion WHERE cobro_id=?", (previous["id"],)
            )}
            if previous["prestadora"] != provider or previous["referencia"] != reference or previous_ids != set(ids) or previous["medio"] != medio:
                raise BusinessError("Este identificador ya corresponde a otro cobro.", 409)
            return receipt(db, previous["id"])
        account = require_account(db, provider, reference)
        cash = db.execute("SELECT id,empleado_id FROM caja WHERE cerrada_en IS NULL").fetchone()
        if not cash:
            raise BusinessError("Abrí la caja antes de cobrar.", 409)
        if employee_id is not None and cash["empleado_id"] != employee_id:
            raise BusinessError("Esta caja pertenece a otro empleado.", 403)
        placeholders = ",".join("?" for _ in ids)
        invoices = db.execute(
            f"SELECT id,importe,estado FROM prestadora.factura WHERE prestadora=? AND referencia=? AND id IN ({placeholders})",
            (provider, reference, *ids),
        ).fetchall()
        if len(invoices) != len(ids) or any(i["estado"] != "PENDIENTE" for i in invoices):
            raise BusinessError("Alguna factura no existe para esta cuenta o ya está pagada. Consultá de nuevo.", 409)
        amount = sum(i["importe"] for i in invoices)
        paid_at, payment_id = now(), str(uuid4())
        db.execute("INSERT INTO prestadora.pago VALUES (?,?,?,?,?,?,?)",
                   (payment_id, request_id, provider, reference, "POS_SIMULADO", amount, paid_at))
        db.executemany("INSERT INTO prestadora.pago_factura VALUES (?,?,?)",
                       [(payment_id, i["id"], i["importe"]) for i in invoices])
        db.executemany("UPDATE prestadora.factura SET estado='PAGADA' WHERE id=?",
                       [(i["id"],) for i in invoices])
        db.execute("INSERT INTO cobro VALUES (?,?,?,?,?,?,?,?,?)",
                   (payment_id, request_id, cash["id"], provider, reference,
                    account["titular"], amount, medio, paid_at))
        db.executemany("INSERT INTO aplicacion VALUES (?,?,?)",
                       [(payment_id, i["id"], i["importe"]) for i in invoices])
        return receipt(db, payment_id)


def receipt(db, payment_id):
    result = rowdict(db.execute(
        "SELECT cobro.*,empleado.nombre AS empleado FROM cobro "
        "JOIN caja ON caja.id=cobro.caja_id JOIN empleado ON empleado.id=caja.empleado_id "
        "WHERE cobro.id=?", (payment_id,)
    ).fetchone())
    result["applications"] = [rowdict(r) for r in db.execute(
        "SELECT factura_id,importe FROM aplicacion WHERE cobro_id=? ORDER BY factura_id", (payment_id,)
    )]
    return result


def pos_history(employee_id, provider=None, reference=None):
    with connect() as db:
        query = ("SELECT cobro.id,cobro.prestadora,cobro.referencia,cobro.titular,cobro.importe,"
                 "cobro.confirmado_en,cobro.medio,empleado.nombre AS empleado FROM cobro "
                 "JOIN caja ON caja.id=cobro.caja_id JOIN empleado ON empleado.id=caja.empleado_id WHERE caja.empleado_id=?")
        args = [employee_id]
        if provider:
            query += " AND cobro.prestadora=?"
            args.append(provider)
        if reference:
            query += " AND cobro.referencia=?"
            args.append(reference.strip().upper())
        query += " ORDER BY cobro.confirmado_en DESC,cobro.id DESC LIMIT 100"
        return [rowdict(r) for r in db.execute(query, args)]


def start_qr(payload, employee_id):
    if not MOBILE_BASE:
        raise BusinessError("El acceso desde celular no está habilitado en este servidor.", 503)
    provider = payload.get("provider")
    reference = str(payload.get("reference") or "").strip().upper()
    ids = payload.get("invoice_ids")
    if not isinstance(ids, list) or not ids or len(ids) > 30 or not all(isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
        raise BusinessError("Seleccioná facturas válidas sin repetir.")
    with LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        require_account(db, provider, reference)
        cash = db.execute("SELECT id FROM caja WHERE cerrada_en IS NULL AND empleado_id=?", (employee_id,)).fetchone()
        if not cash:
            raise BusinessError("Abrí tu caja antes de generar el QR.", 409)
        placeholders = ",".join("?" for _ in ids)
        invoices = db.execute(
            f"SELECT id,importe FROM prestadora.factura WHERE prestadora=? AND referencia=? AND estado='PENDIENTE' AND id IN ({placeholders})",
            (provider, reference, *ids),
        ).fetchall()
        if len(invoices) != len(ids):
            raise BusinessError("Alguna factura ya no está pendiente. Consultá de nuevo.", 409)
        token = secrets.token_urlsafe(24)
        request_id = str(uuid4())
        expires = int(time()) + QR_SECONDS
        amount = sum(invoice["importe"] for invoice in invoices)
        db.execute("INSERT INTO qr_solicitud VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (request_id, hashlib.sha256(token.encode()).hexdigest(), cash["id"],
                    provider, reference, json.dumps(ids), amount, expires, "PENDIENTE", None))
    url = f"{MOBILE_BASE}/qr/{token}"
    svg = io.BytesIO()
    segno.make_qr(url, error="m").save(svg, kind="svg", scale=5, border=3)
    return {"id": request_id, "url": url, "svg": base64.b64encode(svg.getvalue()).decode(),
            "expires_at": expires, "amount": amount}


def qr_status(request_id, employee_id):
    with LOCK, connect() as db:
        row = db.execute(
            "SELECT q.*,c.empleado_id FROM qr_solicitud q JOIN caja c ON c.id=q.caja_id WHERE q.id=?",
            (request_id,),
        ).fetchone()
        if not row or row["empleado_id"] != employee_id:
            raise BusinessError("Solicitud QR no encontrada.", 404)
        if row["estado"] == "PENDIENTE":
            previous = db.execute("SELECT id FROM cobro WHERE solicitud_id=?", (request_id,)).fetchone()
            if previous:
                db.execute("UPDATE qr_solicitud SET estado='CONFIRMADA',cobro_id=? WHERE id=?", (previous["id"], request_id))
                return {"status": "CONFIRMADA", "receipt": receipt(db, previous["id"])}
            if row["expira_en"] <= int(time()):
                db.execute("UPDATE qr_solicitud SET estado='CANCELADA' WHERE id=?", (request_id,))
                return {"status": "CANCELADA"}
        return {"status": row["estado"],
                "receipt": receipt(db, row["cobro_id"]) if row["cobro_id"] else None}


def cancel_qr(request_id, employee_id):
    with LOCK, connect() as db:
        row = db.execute("SELECT q.estado,c.empleado_id FROM qr_solicitud q JOIN caja c ON c.id=q.caja_id WHERE q.id=?", (request_id,)).fetchone()
        if not row or row["empleado_id"] != employee_id:
            raise BusinessError("Solicitud QR no encontrada.", 404)
        if row["estado"] == "PENDIENTE":
            previous = db.execute("SELECT id FROM cobro WHERE solicitud_id=?", (request_id,)).fetchone()
            if previous:
                db.execute("UPDATE qr_solicitud SET estado='CONFIRMADA',cobro_id=? WHERE id=?", (previous["id"], request_id))
            else:
                db.execute("UPDATE qr_solicitud SET estado='CANCELADA' WHERE id=?", (request_id,))
    return qr_status(request_id, employee_id)


def mobile_qr(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        row = db.execute("SELECT q.*,c.cerrada_en FROM qr_solicitud q JOIN caja c ON c.id=q.caja_id WHERE q.token_hash=?", (token_hash,)).fetchone()
        if not row:
            raise BusinessError("QR no encontrado.", 404)
        status = "CANCELADA" if row["estado"] == "PENDIENTE" and (row["expira_en"] <= int(time()) or row["cerrada_en"]) else row["estado"]
        return {"id": row["id"], "provider": row["prestadora"], "reference": row["referencia"],
                "invoice_ids": json.loads(row["facturas_json"]), "amount": row["importe"], "status": status}


def confirm_mobile_qr(token):
    with LOCK:
        data = mobile_qr(token)
        if data["status"] == "CONFIRMADA":
            return data
        if data["status"] != "PENDIENTE":
            raise BusinessError("Este QR venció o fue cancelado.", 409)
        with connect() as db:
            employee_id = db.execute("SELECT c.empleado_id FROM qr_solicitud q JOIN caja c ON c.id=q.caja_id WHERE q.id=?", (data["id"],)).fetchone()[0]
        result = charge({"provider": data["provider"], "reference": data["reference"],
                         "invoice_ids": data["invoice_ids"], "request_id": data["id"],
                         "medio": "QR"}, employee_id, allow_qr=True)
        with connect() as db:
            db.execute("UPDATE qr_solicitud SET estado='CONFIRMADA',cobro_id=? WHERE id=?", (result["id"], data["id"]))
        return mobile_qr(token)


class Handler(BaseHTTPRequestHandler):
    def token(self):
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            return cookies["pos_session"].value if "pos_session" in cookies else None
        except Exception:
            return None

    def authenticated_employee(self):
        employee = session_employee(self.token())
        if not employee:
            raise BusinessError("Iniciá sesión para continuar.", 401)
        return employee

    def send_json(self, status, data, cookie=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path)
        q = parse_qs(path.query)
        try:
            if path.path == "/":
                body = (ROOT / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path.path == "/api/bootstrap":
                employee = session_employee(self.token())
                self.send_json(200, {"providers": PROVIDERS, "employees": employees(),
                                     "methods": [{"code": "EFECTIVO", "name": "Efectivo"},
                                                 {"code": "TARJETA", "name": "Tarjeta (registro simulado)"},
                                                 {"code": "QR", "name": "QR desde celular (simulado)"}],
                                     "employee": employee, "cash": current_cash() if employee else None})
            elif path.path == "/api/lookup":
                self.authenticated_employee()
                self.send_json(200, lookup(q.get("provider", [""])[0], q.get("reference", [""])[0]))
            elif path.path == "/api/history":
                employee = self.authenticated_employee()
                self.send_json(200, pos_history(employee["id"], q.get("provider", [None])[0], q.get("reference", [None])[0]))
            elif path.path == "/api/qr/status":
                employee = self.authenticated_employee()
                self.send_json(200, qr_status(q.get("id", [""])[0], employee["id"]))
            else:
                self.send_json(404, {"error": "Ruta no encontrada."})
        except BusinessError as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "Error interno. Revisá la consola del servidor."})
            raise

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 20000:
                raise BusinessError("Solicitud demasiado grande.", 413)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise BusinessError("JSON inválido.")
            if self.path == "/api/login":
                token, employee = login(payload.get("employee_id"), payload.get("pin"))
                self.send_json(200, {"employee": employee, "cash": current_cash()},
                               f"pos_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}")
                return
            employee = self.authenticated_employee()
            if self.path == "/api/logout":
                logout(self.token(), employee["id"])
                self.send_json(200, {"ok": True}, "pos_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
                return
            if self.path == "/api/cash/open":
                result = open_cash(employee["id"])
            elif self.path == "/api/cash/close":
                result = close_cash(employee["id"])
            elif self.path == "/api/charge":
                result = charge(payload, employee["id"])
            elif self.path == "/api/qr/start":
                result = start_qr(payload, employee["id"])
            elif self.path == "/api/qr/cancel":
                result = cancel_qr(payload.get("id"), employee["id"])
            else:
                self.send_json(404, {"error": "Ruta no encontrada."})
                return
            self.send_json(200, result)
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "JSON inválido."})
        except BusinessError as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "Error interno. Revisá la consola del servidor."})
            raise


class MobileHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        # La URL contiene una capacidad temporal; no registrarla en la consola.
        pass

    def token_from_path(self, confirm=False):
        parts = urlsplit(self.path).path.split("/")
        if len(parts) != (4 if confirm else 3) or parts[1] != "qr":
            return None
        if confirm and parts[3] != "confirm":
            return None
        token = parts[2]
        return token if len(token) == 32 and all(c.isalnum() or c in "-_" for c in token) else None

    def send_page(self, status, data=None, error=None):
        if data:
            amount = f"Gs. {data['amount']:,}".replace(",", ".")
            title = "Pago simulado confirmado" if data["status"] == "CONFIRMADA" else "Confirmar pago simulado"
            details = (f"<dl><dt>Servicio</dt><dd>{html.escape(data['provider'])}</dd>"
                       f"<dt>Referencia</dt><dd>{html.escape(data['reference'])}</dd>"
                       f"<dt>Facturas</dt><dd>{html.escape(', '.join(data['invoice_ids']))}</dd>"
                       f"<dt>Total</dt><dd>{amount}</dd></dl>")
            action = (f"<form method='post' action='/qr/{html.escape(self.token_from_path() or self.token_from_path(True))}/confirm'>"
                      "<button type='submit'>Confirmar pago simulado</button></form>") if data["status"] == "PENDIENTE" else "<p>Volvé al mostrador.</p>"
            if data["status"] == "CANCELADA":
                title, action = "QR vencido o cancelado", "<p>Pedí al cajero que genere uno nuevo.</p>"
        else:
            title, details, action = "No se pudo confirmar", "", f"<p>{html.escape(error or 'Solicitud inválida.')}</p>"
        body = ("<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{title}</title><style>body{{font:16px Arial,sans-serif;background:#eee;color:#222;margin:0;padding:28px}}"
                "main{max-width:450px;margin:auto;background:white;border:1px solid #aaa;padding:20px}h1{font-size:22px}"
                "dt{color:#555;margin-top:15px}dd{margin:2px 0;font-weight:bold}button{padding:12px;width:100%;font:inherit}"
                "small{color:#555}</style></head><body><main><small>POS DE COBRANZAS · DEMOSTRACIÓN</small>"
                f"<h1>{title}</h1><p>Esto no realiza un pago bancario ni mueve dinero.</p>{details}{action}</main></body></html>")
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        token = self.token_from_path()
        if not token:
            self.send_page(404, error="Página no encontrada.")
            return
        try:
            self.send_page(200, mobile_qr(token))
        except BusinessError as exc:
            self.send_page(exc.status, error=str(exc))

    def do_POST(self):
        token = self.token_from_path(confirm=True)
        if not token:
            self.send_page(404, error="Página no encontrada.")
            return
        try:
            self.send_page(200, confirm_mobile_qr(token))
        except BusinessError as exc:
            self.send_page(exc.status, error=str(exc))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="POS de cobranzas simulado")
    parser.add_argument("--mobile-host", help="IPv4 del equipo en la red local para abrir la página QR al celular")
    parser.add_argument("--mobile-port", type=int, default=8766)
    args = parser.parse_args()
    init_db()
    if args.mobile_host:
        socket.inet_aton(args.mobile_host)
        MOBILE_BASE = f"http://{args.mobile_host}:{args.mobile_port}"
        mobile = ThreadingHTTPServer((args.mobile_host, args.mobile_port), MobileHandler)
        threading.Thread(target=mobile.serve_forever, daemon=True).start()
        print(f"QR de demostración para celular en {MOBILE_BASE}", flush=True)
    print("MVP disponible en http://127.0.0.1:8765")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
