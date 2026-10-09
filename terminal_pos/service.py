"""Casos de uso del POS. Ninguna decisión financiera depende de la interfaz."""

from datetime import datetime, timedelta, timezone
import hmac
import secrets
import sqlite3
import uuid

from .db import connect, pin_hash, transaction, utc_now


class PosError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def as_dict(row):
    return dict(row) if row is not None else None


def required_id(value, label):
    if not isinstance(value, str) or not value or len(value) > 100:
        raise PosError("INVALID_INPUT", f"{label} debe tener entre 1 y 100 caracteres")
    return value


def required_amount(value):
    if type(value) is not int or not 1 <= value <= 100_000_000:
        raise PosError("INVALID_AMOUNT", "El importe debe ser un entero entre 1 y 100.000.000 Gs.")
    return value


def open_batch(directory, operator_id=1):
    with connect(directory) as db, transaction(db):
        operator = db.execute("SELECT * FROM operator WHERE id=? AND active=1", (operator_id,)).fetchone()
        if operator is None:
            raise PosError("UNKNOWN_OPERATOR", "Operador no disponible", 404)
        existing = db.execute("SELECT * FROM batch WHERE terminal_id='POS-001' AND closed_at IS NULL").fetchone()
        if existing:
            return as_dict(existing)
        merchant = db.execute("SELECT merchant_name FROM terminal WHERE id='POS-001'").fetchone()
        db.execute("""INSERT INTO batch(terminal_id,operator_id,merchant_name,operator_name,opened_at)
            VALUES ('POS-001',?,?,?,?)""",
            (operator_id, merchant["merchant_name"], operator["name"], utc_now()))
        return as_dict(db.execute("SELECT * FROM batch WHERE id=last_insert_rowid()").fetchone())


def _open_batch(db):
    batch = db.execute("SELECT * FROM batch WHERE terminal_id='POS-001' AND closed_at IS NULL").fetchone()
    if batch is None:
        raise PosError("BATCH_CLOSED", "Primero abrí el lote de la terminal", 409)
    return batch


def _operation(db, request_id):
    return db.execute("SELECT * FROM operation WHERE request_id=?", (request_id,)).fetchone()


def _new_operation(db, request_id, amount, kind, card_id=None, operator_id=None):
    old = _operation(db, request_id)
    if old:
        if operator_id is not None and old["operator_id"] != operator_id:
            raise PosError("NOT_FOUND", "Operación no encontrada", 404)
        if old["kind"] != kind or old["amount"] != amount or old["card_id"] != card_id:
            raise PosError("REQUEST_CONFLICT", "Ese identificador ya pertenece a otra venta", 409)
        return old
    batch = _open_batch(db)
    operator_id = operator_id if operator_id is not None else batch["operator_id"]
    operator_name = db.execute("SELECT name FROM operator WHERE id=? AND active=1", (operator_id,)).fetchone()
    if operator_name is None:
        raise PosError("UNKNOWN_OPERATOR", "Operador no disponible", 404)
    operation_id = uuid.uuid4().hex
    db.execute("""INSERT INTO operation
        (id,request_id,batch_id,operator_id,operator_name,kind,status,amount,card_id,created_at)
        VALUES (?,?,?,?,?,?,'PENDING',?,?,?)""",
        (operation_id, request_id, batch["id"], operator_id, operator_name["name"], kind, amount, card_id, utc_now()))
    return _operation(db, request_id)


def start_card(directory, request_id, amount, card_id, operator_id=None):
    request_id = required_id(request_id, "request_id")
    card_id = required_id(card_id, "card_id")
    amount = required_amount(amount)
    with connect(directory) as db, transaction(db):
        return as_dict(_new_operation(db, request_id, amount, "CARD", card_id, operator_id))


def demo_cards(directory):
    """Solo datos de prueba públicos; nunca saldo, PIN ni identidad bancaria."""
    with connect(directory) as db:
        cards = [dict(row) for row in db.execute("""SELECT c.id, a.kind AS type, c.last4,
            c.active, c.blocked, c.force_chip
            FROM emisor.card c JOIN emisor.account a ON a.id=c.account_id ORDER BY c.id""")]
        for card in cards:
            card["installments"] = [row[0] for row in db.execute(
                "SELECT installments FROM emisor.installment_plan WHERE card_id=? ORDER BY installments", (card["id"],))] or [1]
        return cards


def _finish(db, operation_id, status, response_code, **fields):
    allowed = {"card_type", "last4", "read_method", "installments", "verification", "authorization_code"}
    if not set(fields) <= allowed:
        raise ValueError("Campo de operación no permitido")
    columns = ", ".join(f"{name}=?" for name in fields)
    db.execute(f"""UPDATE operation SET status=?,response_code=?,completed_at=?
        {', ' + columns if columns else ''} WHERE id=?""",
        (status, response_code, utc_now(), *fields.values(), operation_id))
    return as_dict(db.execute("SELECT * FROM operation WHERE id=?", (operation_id,)).fetchone())


def submit_card(directory, request_id, read_method, installments=1, pin=None, attempt_id=None, operator_id=None):
    request_id = required_id(request_id, "request_id")
    if read_method not in {"CONTACTLESS", "CHIP", "MAGSTRIPE"}:
        raise PosError("INVALID_READ_METHOD", "Lectura no admitida")
    if type(installments) is not int or installments < 1:
        raise PosError("INVALID_INSTALLMENTS", "Cantidad de cuotas inválida")
    if pin is not None and (not isinstance(pin, str) or not pin.isdigit() or len(pin) != 4):
        raise PosError("INVALID_PIN", "El PIN ficticio debe tener cuatro dígitos")
    with connect(directory) as db, transaction(db):
        op = _operation(db, request_id)
        if op is None or op["kind"] != "CARD" or (operator_id is not None and op["operator_id"] != operator_id):
            raise PosError("NOT_FOUND", "Venta con tarjeta no encontrada", 404)
        if op["status"] != "PENDING":
            return as_dict(op)
        _open_batch(db)
        card = db.execute("""SELECT c.*, a.kind AS card_type, a.available
            FROM emisor.card c JOIN emisor.account a ON a.id=c.account_id WHERE c.id=?""",
            (op["card_id"],)).fetchone()
        if card is None:
            return _finish(db, op["id"], "DECLINED", "UNKNOWN_CARD")
        if not card["active"] or card["blocked"]:
            return _finish(db, op["id"], "DECLINED", "CARD_BLOCKED", card_type=card["card_type"], last4=card["last4"])
        if read_method == "CONTACTLESS" and card["force_chip"]:
            return {**as_dict(op), "action": "INSERT_CARD"}
        plan = db.execute("SELECT 1 FROM emisor.installment_plan WHERE card_id=? AND installments=?",
                          (card["id"], installments)).fetchone()
        if (card["card_type"] == "DEBIT" and installments != 1) or (card["card_type"] == "CREDIT" and not plan):
            raise PosError("PLAN_UNAVAILABLE", "Cuotas no disponibles para esta tarjeta", 409)
        pin_required = (read_method == "CHIP" and bool(card["chip_pin"])) or (
            read_method == "MAGSTRIPE" and bool(card["magstripe_pin"])) or (
            read_method == "CONTACTLESS" and card["contactless_pin_from"] is not None
            and op["amount"] >= card["contactless_pin_from"])
        if pin_required:
            if pin is None:
                return {**as_dict(op), "action": "PIN_REQUIRED", "attempts_remaining": 3 - card["failed_pins"]}
            attempt_id = required_id(attempt_id, "attempt_id")
            previous = db.execute("SELECT * FROM pin_attempt WHERE operation_id=? AND attempt_id=?",
                                  (op["id"], attempt_id)).fetchone()
            if previous:
                return {**as_dict(op), "action": previous["outcome"], "attempts_remaining": previous["remaining"]}
            correct = hmac.compare_digest(pin_hash(pin, card["pin_salt"]), card["pin_digest"])
            if not correct:
                failures = card["failed_pins"] + 1
                remaining = max(0, 3 - failures)
                outcome = "PIN_RETRY" if remaining else "CARD_BLOCKED"
                db.execute("UPDATE emisor.card SET failed_pins=?,blocked=? WHERE id=?",
                           (failures, int(remaining == 0), card["id"]))
                db.execute("INSERT INTO pin_attempt VALUES (?,?,?,?)", (op["id"], attempt_id, outcome, remaining))
                if remaining:
                    return {**as_dict(op), "action": outcome, "attempts_remaining": remaining}
                return _finish(db, op["id"], "DECLINED", "PIN_LIMIT", card_type=card["card_type"], last4=card["last4"])
            db.execute("UPDATE emisor.card SET failed_pins=0 WHERE id=?", (card["id"],))
            db.execute("INSERT INTO pin_attempt VALUES (?,?,?,3)", (op["id"], attempt_id, "PIN_OK"))
        if card["available"] < op["amount"]:
            return _finish(db, op["id"], "DECLINED", "INSUFFICIENT_FUNDS" if card["card_type"] == "DEBIT" else "LIMIT_EXCEEDED",
                           card_type=card["card_type"], last4=card["last4"], read_method=read_method,
                           installments=installments, verification="PIN" if pin_required else "NO_PIN")
        db.execute("UPDATE emisor.account SET available=available-? WHERE id=?", (op["amount"], card["account_id"]))
        return _finish(db, op["id"], "APPROVED", "APPROVED", card_type=card["card_type"],
                       last4=card["last4"], read_method=read_method, installments=installments,
                       verification="PIN" if pin_required else "NO_PIN",
                       authorization_code=secrets.token_hex(3).upper())


def cancel_card(directory, request_id, operator_id=None):
    """Cancela una lectura pendiente; una venta ya resuelta conserva su resultado."""
    with connect(directory) as db, transaction(db):
        op = _operation(db, required_id(request_id, "request_id"))
        if op is None or op["kind"] != "CARD" or (operator_id is not None and op["operator_id"] != operator_id):
            raise PosError("NOT_FOUND", "Venta con tarjeta no encontrada", 404)
        if op["status"] == "PENDING":
            return _finish(db, op["id"], "CANCELLED", "CARD_CANCELLED")
        return as_dict(op)


def start_qr(directory, request_id, amount, expires_in_seconds=300, operator_id=None):
    request_id = required_id(request_id, "request_id")
    amount = required_amount(amount)
    if type(expires_in_seconds) is not int or not 1 <= expires_in_seconds <= 900:
        raise PosError("INVALID_EXPIRY", "Vencimiento inválido")
    with connect(directory) as db, transaction(db):
        op = _new_operation(db, request_id, amount, "QR", operator_id=operator_id)
        qr = db.execute("SELECT * FROM qr_request WHERE operation_id=?", (op["id"],)).fetchone()
        if qr is None:
            token = secrets.token_urlsafe(24)
            expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).replace(microsecond=0).isoformat()
            db.execute("INSERT INTO qr_request VALUES (?,?,?)", (token, op["id"], expiry))
            qr = db.execute("SELECT * FROM qr_request WHERE token=?", (token,)).fetchone()
        return {"operation": as_dict(op), "token": qr["token"], "expires_at": qr["expires_at"]}


def qr_details(directory, token):
    with connect(directory) as db, transaction(db):
        row = db.execute("""SELECT q.token,q.expires_at,o.id,o.amount,o.status,
            COALESCE(b.merchant_name,t.merchant_name) AS merchant_name
            FROM qr_request q JOIN operation o ON o.id=q.operation_id
            JOIN batch b ON b.id=o.batch_id JOIN terminal t ON t.id=b.terminal_id
            WHERE q.token=?""", (token,)).fetchone()
        if row is None:
            raise PosError("NOT_FOUND", "QR no encontrado", 404)
        if row["status"] == "PENDING" and datetime.now(timezone.utc) >= datetime.fromisoformat(row["expires_at"]):
            _finish(db, row["id"], "EXPIRED", "QR_EXPIRED")
            row = db.execute("""SELECT q.token,q.expires_at,o.id,o.amount,o.status,
                COALESCE(b.merchant_name,t.merchant_name) AS merchant_name
                FROM qr_request q JOIN operation o ON o.id=q.operation_id
                JOIN batch b ON b.id=o.batch_id JOIN terminal t ON t.id=b.terminal_id
                WHERE q.token=?""", (token,)).fetchone()
        return as_dict(row)


def confirm_qr(directory, token):
    token = required_id(token, "token")
    with connect(directory) as db, transaction(db):
        row = db.execute("""SELECT q.expires_at,o.* FROM qr_request q
            JOIN operation o ON o.id=q.operation_id WHERE q.token=?""", (token,)).fetchone()
        if row is None:
            raise PosError("NOT_FOUND", "QR no encontrado", 404)
        if row["status"] != "PENDING":
            return as_dict(db.execute("SELECT * FROM operation WHERE id=?", (row["id"],)).fetchone())
        if datetime.now(timezone.utc) >= datetime.fromisoformat(row["expires_at"]):
            return _finish(db, row["id"], "EXPIRED", "QR_EXPIRED")
        try:
            _open_batch(db)
        except PosError:
            return _finish(db, row["id"], "CANCELLED", "BATCH_CLOSED")
        return _finish(db, row["id"], "APPROVED", "APPROVED", authorization_code=secrets.token_hex(3).upper())


def cancel_qr(directory, request_id, operator_id=None):
    with connect(directory) as db, transaction(db):
        op = _operation(db, required_id(request_id, "request_id"))
        if op is None or op["kind"] != "QR" or (operator_id is not None and op["operator_id"] != operator_id):
            raise PosError("NOT_FOUND", "QR no encontrado", 404)
        if op["status"] == "PENDING":
            return _finish(db, op["id"], "CANCELLED", "QR_CANCELLED")
        return as_dict(op)


def void_sale(directory, request_id, original_id, operator_id=None):
    request_id = required_id(request_id, "request_id")
    original_id = required_id(original_id, "original_id")
    with connect(directory) as db, transaction(db):
        old = _operation(db, request_id)
        if old:
            if operator_id is not None and old["operator_id"] != operator_id:
                raise PosError("NOT_FOUND", "Operación no encontrada", 404)
            if old["kind"] != "VOID" or old["original_id"] != original_id:
                raise PosError("REQUEST_CONFLICT", "Identificador de anulación en uso", 409)
            return as_dict(old)
        batch = _open_batch(db)
        sale = db.execute("SELECT * FROM operation WHERE id=?", (original_id,)).fetchone()
        if sale is None or sale["status"] != "APPROVED" or sale["kind"] not in {"CARD", "QR"} or (operator_id is not None and sale["operator_id"] != operator_id):
            raise PosError("NOT_VOIDABLE", "Venta aprobada no encontrada", 409)
        if sale["batch_id"] != batch["id"]:
            raise PosError("BATCH_MISMATCH", "Solo se anulan ventas del lote abierto", 409)
        if db.execute("SELECT 1 FROM operation WHERE kind='VOID' AND original_id=?", (original_id,)).fetchone():
            raise PosError("ALREADY_VOIDED", "La venta ya fue anulada", 409)
        if sale["kind"] == "CARD":
            account = db.execute("SELECT account_id FROM emisor.card WHERE id=?", (sale["card_id"],)).fetchone()
            db.execute("UPDATE emisor.account SET available=available+? WHERE id=?", (sale["amount"], account["account_id"]))
        operation_id = uuid.uuid4().hex
        operator_id = operator_id if operator_id is not None else batch["operator_id"]
        operator_name = db.execute("SELECT name FROM operator WHERE id=?", (operator_id,)).fetchone()["name"]
        db.execute("""INSERT INTO operation
            (id,request_id,batch_id,operator_id,operator_name,kind,status,amount,card_id,card_type,last4,original_id,
             response_code,authorization_code,created_at,completed_at)
            VALUES (?,?,?,?,?,'VOID','APPROVED',?,?,?,?,?,'VOIDED',?,?,?)""",
            (operation_id, request_id, batch["id"], operator_id, operator_name, sale["amount"], sale["card_id"],
             sale["card_type"], sale["last4"], original_id, secrets.token_hex(3).upper(), utc_now(), utc_now()))
        return as_dict(db.execute("SELECT * FROM operation WHERE id=?", (operation_id,)).fetchone())


def batch_summary(directory, batch_id=None):
    with connect(directory) as db:
        if batch_id is None:
            batch = db.execute("SELECT * FROM batch ORDER BY id DESC LIMIT 1").fetchone()
        else:
            batch = db.execute("SELECT * FROM batch WHERE id=?", (batch_id,)).fetchone()
        if batch is None:
            raise PosError("NOT_FOUND", "Lote no encontrado", 404)
        rows = [as_dict(row) for row in db.execute("""SELECT kind,COALESCE(card_type,'QR') AS payment_type,
            COUNT(*) AS count,SUM(amount) AS amount FROM operation
            WHERE batch_id=? AND status='APPROVED' GROUP BY kind,card_type""", (batch["id"],))]
        sales = sum(r["amount"] for r in rows if r["kind"] != "VOID")
        voids = sum(r["amount"] for r in rows if r["kind"] == "VOID")
        return {"batch": as_dict(batch), "groups": rows, "sales": sales, "voids": voids, "net": sales - voids}


def close_batch(directory):
    with connect(directory) as db, transaction(db):
        batch = _open_batch(db)
        db.execute("""UPDATE operation SET status='CANCELLED',response_code='BATCH_CLOSED',
            completed_at=? WHERE batch_id=? AND status='PENDING'""", (utc_now(), batch["id"]))
        db.execute("UPDATE batch SET closed_at=? WHERE id=?", (utc_now(), batch["id"]))
        batch_id = batch["id"]
    return batch_summary(directory, batch_id)


def operation(directory, operation_id, operator_id=None):
    with connect(directory) as db:
        row = db.execute("SELECT * FROM operation WHERE id=?", (operation_id,)).fetchone()
        if row is None or (operator_id is not None and row["operator_id"] != operator_id):
            raise PosError("NOT_FOUND", "Operación no encontrada", 404)
        return as_dict(row)


def operation_by_request(directory, request_id, kind=None, operator_id=None):
    with connect(directory) as db, transaction(db):
        row = _operation(db, required_id(request_id, "request_id"))
        if row is None or (kind is not None and row["kind"] != kind) or (operator_id is not None and row["operator_id"] != operator_id):
            raise PosError("NOT_FOUND", "Operación no encontrada", 404)
        if row["kind"] == "QR" and row["status"] == "PENDING":
            qr = db.execute("SELECT expires_at FROM qr_request WHERE operation_id=?", (row["id"],)).fetchone()
            if qr and datetime.now(timezone.utc) >= datetime.fromisoformat(qr["expires_at"]):
                return _finish(db, row["id"], "EXPIRED", "QR_EXPIRED")
        return as_dict(row)


def operations(directory, batch_id=None, operator_id=None):
    with connect(directory) as db:
        conditions = []
        values = []
        if batch_id is not None:
            conditions.append("batch_id=?")
            values.append(batch_id)
        if operator_id is not None:
            conditions.append("operator_id=?")
            values.append(operator_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return [as_dict(r) for r in db.execute("SELECT * FROM operation" + where + " ORDER BY created_at DESC,id DESC LIMIT 100", values)]


def ticket(directory, operation_id, operator_id=None):
    op = operation(directory, operation_id, operator_id)
    if op["status"] != "APPROVED":
        raise PosError("NO_TICKET", "Solo una operación aprobada tiene comprobante", 409)
    with connect(directory) as db:
        row = db.execute("""SELECT COALESCE(b.merchant_name,t.merchant_name) AS merchant_name,
            t.id AS terminal_id,COALESCE(o.operator_name,b.operator_name) AS operator_name
            FROM batch b JOIN terminal t ON t.id=b.terminal_id
            JOIN operation o ON o.id=? WHERE b.id=?""", (op["id"], op["batch_id"])).fetchone()
    return {"ticket_number": op["id"], "merchant": row["merchant_name"],
            "terminal": row["terminal_id"], "operator": row["operator_name"],
            "issued_at": op["completed_at"], "amount": op["amount"], "status": op["status"],
            "kind": op["kind"], "card_type": op["card_type"], "last4": op["last4"],
            "installments": op["installments"], "read_method": op["read_method"],
            "verification": op["verification"],
            "authorization_code": op["authorization_code"], "original_id": op["original_id"],
            "legend": "SIMULACIÓN EDUCATIVA — SIN DINERO REAL"}
