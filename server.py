"""POS de cobranzas educativo. Prestadoras, facturas y confirmaciones son simuladas."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LOCK = threading.RLock()
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
                id INTEGER PRIMARY KEY, nombre TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS caja (
                id INTEGER PRIMARY KEY, empleado_id INTEGER NOT NULL REFERENCES empleado(id),
                abierta_en TEXT NOT NULL, cerrada_en TEXT
            );
            CREATE TABLE IF NOT EXISTS cobro (
                id TEXT PRIMARY KEY, solicitud_id TEXT NOT NULL UNIQUE,
                caja_id INTEGER NOT NULL REFERENCES caja(id),
                prestadora TEXT NOT NULL, referencia TEXT NOT NULL,
                titular TEXT NOT NULL, importe INTEGER NOT NULL CHECK(importe > 0),
                medio TEXT NOT NULL CHECK(medio = 'EFECTIVO'),
                confirmado_en TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS aplicacion (
                cobro_id TEXT NOT NULL REFERENCES cobro(id),
                factura_id TEXT NOT NULL, importe INTEGER NOT NULL CHECK(importe > 0),
                PRIMARY KEY(cobro_id, factura_id)
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
        if db.execute("SELECT COUNT(*) FROM empleado").fetchone()[0] == 0:
            db.executemany("INSERT INTO empleado(id,nombre) VALUES (?,?)", [(1, "Lucía Benítez"), (2, "Diego Rojas")])
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
            "WHERE prestadora=? AND referencia=? ORDER BY vencimiento,id",
            (provider, account["referencia"]),
        )]
        history = [rowdict(r) for r in db.execute(
            "SELECT id,origen,importe,pagado_en FROM prestadora.pago "
            "WHERE prestadora=? AND referencia=? ORDER BY pagado_en DESC",
            (provider, account["referencia"]),
        )]
        return {"account": account, "invoices": invoices, "provider_history": history,
                "status": "PENDIENTE" if any(i["estado"] == "PENDIENTE" for i in invoices) else "SIN_DEUDA"}


def employees():
    with connect() as db:
        return [rowdict(r) for r in db.execute("SELECT * FROM empleado ORDER BY id")]


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


def close_cash():
    with LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        cash = db.execute("SELECT id FROM caja WHERE cerrada_en IS NULL").fetchone()
        if not cash:
            raise BusinessError("No hay caja abierta.", 409)
        total, count = db.execute(
            "SELECT COALESCE(SUM(importe),0),COUNT(*) FROM cobro WHERE caja_id=?", (cash["id"],)
        ).fetchone()
        db.execute("UPDATE caja SET cerrada_en=? WHERE id=?", (now(), cash["id"]))
        return {"caja_id": cash["id"], "cobros": count, "total": total}


def charge(payload):
    provider = payload.get("provider")
    reference = str(payload.get("reference") or "").strip().upper()
    request_id = str(payload.get("request_id") or "").strip()
    ids = payload.get("invoice_ids")
    if not request_id or len(request_id) > 80:
        raise BusinessError("Identificador de solicitud inválido.")
    if not isinstance(ids, list) or not ids or len(ids) > 30 or not all(isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
        raise BusinessError("Seleccioná facturas válidas sin repetir.")
    with LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT * FROM cobro WHERE solicitud_id=?", (request_id,)).fetchone()
        if previous:
            previous_ids = {r["factura_id"] for r in db.execute(
                "SELECT factura_id FROM aplicacion WHERE cobro_id=?", (previous["id"],)
            )}
            if previous["prestadora"] != provider or previous["referencia"] != reference or previous_ids != set(ids):
                raise BusinessError("Este identificador ya corresponde a otro cobro.", 409)
            return receipt(db, previous["id"])
        account = require_account(db, provider, reference)
        cash = db.execute("SELECT id FROM caja WHERE cerrada_en IS NULL").fetchone()
        if not cash:
            raise BusinessError("Abrí la caja antes de cobrar.", 409)
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
                    account["titular"], amount, "EFECTIVO", paid_at))
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


def pos_history(provider=None, reference=None):
    with connect() as db:
        query = ("SELECT cobro.id,cobro.prestadora,cobro.referencia,cobro.titular,cobro.importe,"
                 "cobro.confirmado_en,empleado.nombre AS empleado FROM cobro "
                 "JOIN caja ON caja.id=cobro.caja_id JOIN empleado ON empleado.id=caja.empleado_id WHERE 1=1")
        args = []
        if provider:
            query += " AND cobro.prestadora=?"
            args.append(provider)
        if reference:
            query += " AND cobro.referencia=?"
            args.append(reference.strip().upper())
        query += " ORDER BY cobro.confirmado_en DESC,cobro.id DESC LIMIT 100"
        return [rowdict(r) for r in db.execute(query, args)]


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path.path == "/api/bootstrap":
                self.send_json(200, {"providers": PROVIDERS, "employees": employees(), "cash": current_cash()})
            elif path.path == "/api/lookup":
                self.send_json(200, lookup(q.get("provider", [""])[0], q.get("reference", [""])[0]))
            elif path.path == "/api/history":
                self.send_json(200, pos_history(q.get("provider", [None])[0], q.get("reference", [None])[0]))
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
            if self.path == "/api/cash/open":
                result = open_cash(payload.get("employee_id"))
            elif self.path == "/api/cash/close":
                result = close_cash()
            elif self.path == "/api/charge":
                result = charge(payload)
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


if __name__ == "__main__":
    init_db()
    print("MVP disponible en http://127.0.0.1:8765")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
