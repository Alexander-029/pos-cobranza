"""Esquema y datos ficticios, separados entre comercio y emisor simulado."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import secrets
import sqlite3


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def pin_hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 100_000).hex()


def operator_pin_hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 600_000).hex()


@contextmanager
def connect(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "terminal.db", timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("ATTACH DATABASE ? AS emisor", (str(directory / "emisor_simulado.db"),))
        db.execute("PRAGMA emisor.journal_mode=DELETE")
        yield db
    finally:
        db.close()


@contextmanager
def transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.rollback()
        raise
    else:
        db.commit()


def initialize(directory):
    with connect(directory) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS operator (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                pin_salt TEXT, pin_digest TEXT, failed_logins INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT
            );
            CREATE TABLE IF NOT EXISTS operator_session (
                token_digest TEXT PRIMARY KEY, operator_id INTEGER NOT NULL REFERENCES operator(id),
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS terminal (
                id TEXT PRIMARY KEY, merchant_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batch (
                id INTEGER PRIMARY KEY, terminal_id TEXT NOT NULL REFERENCES terminal(id),
                operator_id INTEGER NOT NULL REFERENCES operator(id),
                merchant_name TEXT, operator_name TEXT,
                opened_at TEXT NOT NULL, closed_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_batch
                ON batch(terminal_id) WHERE closed_at IS NULL;
            CREATE TABLE IF NOT EXISTS operation (
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                batch_id INTEGER NOT NULL REFERENCES batch(id),
                operator_id INTEGER REFERENCES operator(id),
                operator_name TEXT,
                kind TEXT NOT NULL CHECK(kind IN ('CARD','QR','VOID')),
                status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','DECLINED','CANCELLED','EXPIRED')),
                amount INTEGER NOT NULL CHECK(amount > 0),
                card_id TEXT, card_type TEXT, last4 TEXT,
                read_method TEXT, installments INTEGER,
                verification TEXT, response_code TEXT,
                authorization_code TEXT, original_id TEXT REFERENCES operation(id),
                created_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_void_per_sale
                ON operation(original_id) WHERE kind='VOID';
            CREATE TABLE IF NOT EXISTS pin_attempt (
                operation_id TEXT NOT NULL REFERENCES operation(id),
                attempt_id TEXT NOT NULL, outcome TEXT NOT NULL,
                remaining INTEGER NOT NULL,
                PRIMARY KEY(operation_id,attempt_id)
            );
            CREATE TABLE IF NOT EXISTS qr_request (
                token TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE REFERENCES operation(id),
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS emisor.account (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('DEBIT','CREDIT')),
                available INTEGER NOT NULL CHECK(available >= 0)
            );
            CREATE TABLE IF NOT EXISTS emisor.card (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES account(id),
                last4 TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                blocked INTEGER NOT NULL DEFAULT 0,
                pin_salt TEXT NOT NULL, pin_digest TEXT NOT NULL,
                failed_pins INTEGER NOT NULL DEFAULT 0,
                force_chip INTEGER NOT NULL DEFAULT 0,
                chip_pin INTEGER NOT NULL DEFAULT 1,
                contactless_pin_from INTEGER,
                magstripe_pin INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS emisor.installment_plan (
                card_id TEXT NOT NULL REFERENCES card(id), installments INTEGER NOT NULL,
                PRIMARY KEY(card_id,installments)
            );
        """)
        # Conserva bases locales creadas antes de añadir nombres históricos al lote.
        columns = {row[1] for row in db.execute("PRAGMA table_info(batch)")}
        for name in ("merchant_name", "operator_name"):
            if name not in columns:
                db.execute(f"ALTER TABLE batch ADD COLUMN {name} TEXT")
        operator_columns = {row[1] for row in db.execute("PRAGMA table_info(operator)")}
        for name, definition in (
            ("pin_salt", "TEXT"), ("pin_digest", "TEXT"),
            ("failed_logins", "INTEGER NOT NULL DEFAULT 0"), ("locked_until", "TEXT"),
        ):
            if name not in operator_columns:
                db.execute(f"ALTER TABLE operator ADD COLUMN {name} {definition}")
        operation_columns = {row[1] for row in db.execute("PRAGMA table_info(operation)")}
        if "operator_id" not in operation_columns:
            db.execute("ALTER TABLE operation ADD COLUMN operator_id INTEGER REFERENCES operator(id)")
        if "operator_name" not in operation_columns:
            db.execute("ALTER TABLE operation ADD COLUMN operator_name TEXT")
        with transaction(db):
            db.execute("INSERT OR IGNORE INTO operator(id,name) VALUES (1,'Operador demo')")
            db.execute("INSERT OR IGNORE INTO operator(id,name) VALUES (2,'Operador dos')")
            for operator_id, pin in ((1, "111111"), (2, "222222")):
                if db.execute("SELECT pin_digest FROM operator WHERE id=?", (operator_id,)).fetchone()[0] is None:
                    salt = secrets.token_hex(16)
                    db.execute("UPDATE operator SET pin_salt=?,pin_digest=? WHERE id=?",
                               (salt, operator_pin_hash(pin, salt), operator_id))
            db.execute("INSERT OR IGNORE INTO terminal(id,merchant_name) VALUES ('POS-001','Comercio de prueba')")
            db.execute("""UPDATE batch SET merchant_name=(SELECT merchant_name FROM terminal WHERE id=batch.terminal_id)
                WHERE merchant_name IS NULL""")
            db.execute("""UPDATE batch SET operator_name=(SELECT name FROM operator WHERE id=batch.operator_id)
                WHERE operator_name IS NULL""")
            db.execute("""UPDATE operation SET operator_id=(SELECT operator_id FROM batch WHERE batch.id=operation.batch_id)
                WHERE operator_id IS NULL""")
            db.execute("""UPDATE operation SET operator_name=(SELECT operator_name FROM batch WHERE batch.id=operation.batch_id)
                WHERE operator_name IS NULL""")
            demos = [
                # token, account, kind, available, last4, pin, force_chip, contactless_pin_from
                ("DEB-001", "CTA-001", "DEBIT", 150_000, "4101", "1234", 0, 100_000),
                ("DEB-002", "CTA-002", "DEBIT", 80_000, "4102", "4321", 1, None),
                ("CRE-001", "LIN-001", "CREDIT", 200_000, "5201", "2468", 0, None),
                ("CRE-002", "LIN-002", "CREDIT", 50_000, "5202", "1357", 0, 0),
            ]
            for token, account, kind, available, last4, pin, force_chip, pin_from in demos:
                db.execute("INSERT OR IGNORE INTO emisor.account VALUES (?,?,?)", (account, kind, available))
                db.execute("""INSERT OR IGNORE INTO emisor.card
                    (id,account_id,last4,pin_salt,pin_digest,force_chip,contactless_pin_from)
                    VALUES (?,?,?,?,?,?,?)""",
                    (token, account, last4, token + "-demo-salt", pin_hash(pin, token + "-demo-salt"), force_chip, pin_from))
            db.executemany("INSERT OR IGNORE INTO emisor.installment_plan VALUES (?,?)",
                           [("CRE-001", 1), ("CRE-001", 3), ("CRE-002", 1)])
