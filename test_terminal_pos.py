"""Pruebas de las reglas que afectan dinero ficticio y registros persistidos."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3

from terminal_pos.db import connect, initialize
from terminal_pos import service as pos


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=".")
        self.directory = self.tmp.name
        initialize(self.directory)

    def tearDown(self):
        self.tmp.cleanup()

    def available(self, account_id):
        with connect(self.directory) as db:
            return db.execute("SELECT available FROM emisor.account WHERE id=?", (account_id,)).fetchone()[0]

    def sale(self, request_id, amount, card, read_method, **kwargs):
        pos.start_card(self.directory, request_id, amount, card)
        return pos.submit_card(self.directory, request_id, read_method, **kwargs)

    def test_batch_is_required_and_amount_is_integer(self):
        with self.assertRaises(pos.PosError) as error:
            pos.start_card(self.directory, "a", 100, "DEB-001")
        self.assertEqual(error.exception.code, "BATCH_CLOSED")
        pos.open_batch(self.directory)
        for amount in (0, -1, 1.5, True):
            with self.assertRaises(pos.PosError):
                pos.start_card(self.directory, "invalid", amount, "DEB-001")

    def test_debit_pin_then_funds_and_idempotence(self):
        pos.open_batch(self.directory)
        first = self.sale("debit-1", 120_000, "DEB-001", "CONTACTLESS")
        self.assertEqual(first["action"], "PIN_REQUIRED")
        approved = pos.submit_card(self.directory, "debit-1", "CONTACTLESS", pin="1234", attempt_id="try-1")
        self.assertEqual(approved["status"], "APPROVED")
        self.assertEqual(approved["verification"], "PIN")
        self.assertEqual(self.available("CTA-001"), 30_000)
        self.assertEqual(pos.submit_card(self.directory, "debit-1", "CONTACTLESS")["id"], approved["id"])
        self.assertEqual(pos.start_card(self.directory, "debit-1", 120_000, "DEB-001")["id"], approved["id"])
        self.assertEqual(self.available("CTA-001"), 30_000)
        declined = self.sale("debit-2", 50_000, "DEB-001", "CONTACTLESS", pin="1234", attempt_id="try-2")
        self.assertEqual(declined["response_code"], "INSUFFICIENT_FUNDS")
        self.assertEqual(pos.batch_summary(self.directory)["net"], 120_000)

    def test_credit_installments_and_limit(self):
        pos.open_batch(self.directory)
        with self.assertRaises(pos.PosError) as error:
            self.sale("credit-1", 120_000, "CRE-001", "CONTACTLESS", installments=6)
        self.assertEqual(error.exception.code, "PLAN_UNAVAILABLE")
        approved = pos.submit_card(self.directory, "credit-1", "CONTACTLESS", installments=3)
        self.assertEqual(approved["status"], "APPROVED")
        self.assertEqual(approved["installments"], 3)
        self.assertEqual(self.available("LIN-001"), 80_000)
        declined = self.sale("credit-2", 90_000, "CRE-001", "CONTACTLESS")
        self.assertEqual(declined["response_code"], "LIMIT_EXCEEDED")

    def test_contactless_can_require_chip_and_pin_attempts_block_card(self):
        pos.open_batch(self.directory)
        action = self.sale("chip", 10_000, "DEB-002", "CONTACTLESS")
        self.assertEqual(action["action"], "INSERT_CARD")
        self.assertEqual(pos.submit_card(self.directory, "chip", "CHIP")["action"], "PIN_REQUIRED")
        wrong = pos.submit_card(self.directory, "chip", "CHIP", pin="9999", attempt_id="one")
        self.assertEqual(wrong["attempts_remaining"], 2)
        duplicate = pos.submit_card(self.directory, "chip", "CHIP", pin="9999", attempt_id="one")
        self.assertEqual(duplicate["attempts_remaining"], 2)
        self.assertEqual(pos.submit_card(self.directory, "chip", "CHIP", pin="9999", attempt_id="two")["attempts_remaining"], 1)
        blocked = pos.submit_card(self.directory, "chip", "CHIP", pin="9999", attempt_id="three")
        self.assertEqual(blocked["response_code"], "PIN_LIMIT")
        self.assertEqual(self.available("CTA-002"), 80_000)
        self.assertEqual(self.sale("chip-next", 100, "DEB-002", "CHIP")["response_code"], "CARD_BLOCKED")

    def test_pin_correct_does_not_override_insufficient_funds(self):
        pos.open_batch(self.directory)
        declined = self.sale("high", 150_001, "DEB-001", "CHIP", pin="1234", attempt_id="pin")
        self.assertEqual(declined["response_code"], "INSUFFICIENT_FUNDS")

    def test_cancel_pending_card_keeps_balance_and_cannot_later_approve(self):
        pos.open_batch(self.directory)
        pending = self.sale("cancel-card", 120_000, "DEB-001", "CONTACTLESS")
        self.assertEqual(pending["action"], "PIN_REQUIRED")
        cancelled = pos.cancel_card(self.directory, "cancel-card")
        self.assertEqual((cancelled["status"], cancelled["response_code"]), ("CANCELLED", "CARD_CANCELLED"))
        self.assertEqual(pos.cancel_card(self.directory, "cancel-card")["id"], cancelled["id"])
        self.assertEqual(pos.submit_card(self.directory, "cancel-card", "CONTACTLESS",
                                         pin="1234", attempt_id="late")["status"], "CANCELLED")
        self.assertEqual(self.available("CTA-001"), 150_000)
        self.assertEqual(pos.batch_summary(self.directory)["net"], 0)

        approved = self.sale("already-approved", 10_000, "DEB-001", "CONTACTLESS")
        self.assertEqual(pos.cancel_card(self.directory, "already-approved")["id"], approved["id"])
        self.assertEqual(self.available("CTA-001"), 140_000)

    def test_qr_confirmation_once_cancel_and_expire(self):
        pos.open_batch(self.directory)
        qr = pos.start_qr(self.directory, "qr-1", 75_000)
        self.assertEqual(qr["token"], pos.start_qr(self.directory, "qr-1", 75_000)["token"])
        approved = pos.confirm_qr(self.directory, qr["token"])
        self.assertEqual(pos.confirm_qr(self.directory, qr["token"])["id"], approved["id"])
        self.assertEqual(pos.batch_summary(self.directory)["net"], 75_000)
        cancelled = pos.start_qr(self.directory, "qr-2", 30_000)
        self.assertEqual(pos.cancel_qr(self.directory, "qr-2")["status"], "CANCELLED")
        self.assertEqual(pos.confirm_qr(self.directory, cancelled["token"])["status"], "CANCELLED")
        expiring = pos.start_qr(self.directory, "qr-3", 20_000)
        with connect(self.directory) as db:
            db.execute("UPDATE qr_request SET expires_at='2000-01-01T00:00:00+00:00' WHERE token=?", (expiring["token"],))
        self.assertEqual(pos.confirm_qr(self.directory, expiring["token"])["status"], "EXPIRED")
        self.assertEqual(pos.operation_by_request(self.directory, "qr-3", "QR")["status"], "EXPIRED")
        self.assertEqual(pos.batch_summary(self.directory)["net"], 75_000)

    def test_qr_status_expires_without_phone_confirmation(self):
        pos.open_batch(self.directory)
        qr = pos.start_qr(self.directory, "qr-old", 20_000)
        with connect(self.directory) as db:
            db.execute("UPDATE qr_request SET expires_at='2000-01-01T00:00:00+00:00' WHERE token=?", (qr["token"],))
        self.assertEqual(pos.operation_by_request(self.directory, "qr-old", "QR")["status"], "EXPIRED")
        self.assertEqual(pos.qr_details(self.directory, qr["token"])["status"], "EXPIRED")

    def test_same_request_cannot_change_amount_or_card(self):
        pos.open_batch(self.directory)
        pos.start_card(self.directory, "same", 100, "DEB-001")
        with self.assertRaises(pos.PosError) as error:
            pos.start_card(self.directory, "same", 200, "DEB-001")
        self.assertEqual(error.exception.code, "REQUEST_CONFLICT")
        with self.assertRaises(pos.PosError):
            pos.start_qr(self.directory, "same", 100)

    def test_void_restores_funds_and_closed_batch_is_immutable(self):
        pos.open_batch(self.directory)
        approved = self.sale("sale", 50_000, "DEB-001", "CONTACTLESS")
        self.assertEqual(approved["status"], "APPROVED")
        self.assertEqual(self.available("CTA-001"), 100_000)
        voided = pos.void_sale(self.directory, "void-1", approved["id"])
        self.assertEqual(pos.void_sale(self.directory, "void-1", approved["id"])["id"], voided["id"])
        self.assertEqual(self.available("CTA-001"), 150_000)
        with self.assertRaises(pos.PosError):
            pos.void_sale(self.directory, "void-2", approved["id"])
        self.assertEqual(pos.batch_summary(self.directory)["net"], 0)
        self.assertEqual(pos.close_batch(self.directory)["net"], 0)
        with self.assertRaises(pos.PosError):
            pos.start_card(self.directory, "after-close", 100, "DEB-001")
        self.assertIn("SIMULACIÓN", pos.ticket(self.directory, approved["id"])["legend"])

    def test_concurrent_card_retry_debits_once(self):
        pos.open_batch(self.directory)
        pos.start_card(self.directory, "parallel-card", 50_000, "DEB-001")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: pos.submit_card(self.directory, "parallel-card", "CONTACTLESS"), range(8)))
        self.assertEqual({row["id"] for row in results}, {results[0]["id"]})
        self.assertEqual({row["status"] for row in results}, {"APPROVED"})
        self.assertEqual(self.available("CTA-001"), 100_000)
        self.assertEqual(pos.batch_summary(self.directory)["net"], 50_000)

    def test_cancel_and_authorize_race_has_one_final_card_result(self):
        pos.open_batch(self.directory)
        pos.start_card(self.directory, "race-card", 40_000, "DEB-001")
        with ThreadPoolExecutor(max_workers=2) as pool:
            submitted = pool.submit(pos.submit_card, self.directory, "race-card", "CONTACTLESS")
            cancelled = pool.submit(pos.cancel_card, self.directory, "race-card")
            outcomes = [submitted.result(), cancelled.result()]
        self.assertEqual({row["id"] for row in outcomes}, {outcomes[0]["id"]})
        final = pos.operation_by_request(self.directory, "race-card", "CARD")
        self.assertIn(final["status"], {"APPROVED", "CANCELLED"})
        expected = 110_000 if final["status"] == "APPROVED" else 150_000
        self.assertEqual(self.available("CTA-001"), expected)
        self.assertEqual(pos.batch_summary(self.directory)["net"],
                         40_000 if final["status"] == "APPROVED" else 0)

    def test_concurrent_qr_confirmation_credits_once(self):
        pos.open_batch(self.directory)
        qr = pos.start_qr(self.directory, "parallel-qr", 25_000)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: pos.confirm_qr(self.directory, qr["token"]), range(8)))
        self.assertEqual({row["id"] for row in results}, {results[0]["id"]})
        self.assertEqual({row["status"] for row in results}, {"APPROVED"})
        self.assertEqual(pos.batch_summary(self.directory)["net"], 25_000)

    def test_ticket_and_qr_keep_names_from_batch_opening(self):
        pos.open_batch(self.directory)
        approved = self.sale("snapshot", 1_000, "DEB-001", "CONTACTLESS")
        qr = pos.start_qr(self.directory, "snapshot-qr", 2_000)
        with connect(self.directory) as db:
            db.execute("UPDATE terminal SET merchant_name='Nombre nuevo' WHERE id='POS-001'")
            db.execute("UPDATE operator SET name='Otro nombre' WHERE id=1")
        ticket = pos.ticket(self.directory, approved["id"])
        self.assertEqual((ticket["merchant"], ticket["operator"]), ("Comercio de prueba", "Operador demo"))
        self.assertEqual(ticket["status"], "APPROVED")
        self.assertEqual(pos.qr_details(self.directory, qr["token"])["merchant_name"], "Comercio de prueba")
        initialize(self.directory)
        self.assertEqual(pos.ticket(self.directory, approved["id"])["merchant"], "Comercio de prueba")

    def test_existing_batch_schema_migrates_without_losing_batch(self):
        old_db = Path(self.directory) / "terminal.db"
        with closing(sqlite3.connect(old_db)) as db:
            db.executescript("""
                DROP TABLE batch;
                CREATE TABLE batch(id INTEGER PRIMARY KEY,terminal_id TEXT NOT NULL,
                    operator_id INTEGER NOT NULL,opened_at TEXT NOT NULL,closed_at TEXT);
                INSERT INTO batch VALUES (7,'POS-001',1,'2026-01-01T00:00:00+00:00',NULL);
            """)
        initialize(self.directory)
        with connect(self.directory) as db:
            row = db.execute("SELECT * FROM batch WHERE id=7").fetchone()
        self.assertEqual((row["merchant_name"], row["operator_name"]),
                         ("Comercio de prueba", "Operador demo"))

    def test_parallel_distinct_sales_never_overdraw_or_break_relations(self):
        pos.open_batch(self.directory)
        for number in range(8):
            pos.start_card(self.directory, f"stress-{number}", 30_000, "DEB-001")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda number: pos.submit_card(
                self.directory, f"stress-{number}", "CONTACTLESS"), range(8)))
        self.assertEqual(sum(row["status"] == "APPROVED" for row in results), 5)
        self.assertEqual(sum(row["status"] == "DECLINED" for row in results), 3)
        self.assertEqual(self.available("CTA-001"), 0)
        self.assertEqual(pos.batch_summary(self.directory)["net"], 150_000)
        with connect(self.directory) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA emisor.quick_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("PRAGMA emisor.foreign_key_check").fetchall(), [])

    def test_parallel_voids_restore_funds_exactly_once(self):
        pos.open_batch(self.directory)
        sale = self.sale("void-race-sale", 40_000, "DEB-001", "CONTACTLESS")
        def attempt(number):
            try:
                return pos.void_sale(self.directory, f"void-race-{number}", sale["id"])["status"]
            except pos.PosError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(attempt, range(6)))
        self.assertEqual(outcomes.count("APPROVED"), 1)
        self.assertEqual(outcomes.count("ALREADY_VOIDED"), 5)
        self.assertEqual(self.available("CTA-001"), 150_000)
        self.assertEqual(pos.batch_summary(self.directory)["net"], 0)

    def test_amount_boundaries_do_not_create_invalid_debits(self):
        pos.open_batch(self.directory)
        self.assertEqual(self.sale("smallest", 1, "DEB-001", "CONTACTLESS")["status"], "APPROVED")
        self.assertEqual(self.available("CTA-001"), 149_999)
        self.assertEqual(self.sale("largest", 100_000_000, "CRE-001", "CONTACTLESS")["response_code"], "LIMIT_EXCEEDED")
        with self.assertRaises(pos.PosError):
            pos.start_card(self.directory, "too-large", 100_000_001, "DEB-001")
        self.assertEqual(self.available("LIN-001"), 200_000)

    def test_operator_migration_preserves_an_approved_old_payment(self):
        batch = pos.open_batch(self.directory)
        old_db = Path(self.directory) / "terminal.db"
        with closing(sqlite3.connect(old_db)) as db:
            db.executescript("""
                DROP TABLE operation;
                CREATE TABLE operation (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    batch_id INTEGER NOT NULL REFERENCES batch(id),
                    kind TEXT NOT NULL, status TEXT NOT NULL, amount INTEGER NOT NULL,
                    card_id TEXT, card_type TEXT, last4 TEXT, read_method TEXT,
                    installments INTEGER, verification TEXT, response_code TEXT,
                    authorization_code TEXT, original_id TEXT,
                    created_at TEXT NOT NULL, completed_at TEXT
                );
                DROP TABLE operator;
                CREATE TABLE operator(id INTEGER PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
                INSERT INTO operator VALUES (1,'Operador demo',1);
            """)
            db.execute("""INSERT INTO operation
                (id,request_id,batch_id,kind,status,amount,card_id,card_type,last4,
                 read_method,installments,response_code,authorization_code,created_at,completed_at)
                VALUES ('old-payment','old-request',?,'CARD','APPROVED',1000,'DEB-001','DEBIT','4101',
                        'CONTACTLESS',1,'APPROVED','LEGACY','2026-01-01T00:00:00+00:00','2026-01-01T00:00:01+00:00')""",
                       (batch["id"],))
            db.commit()
        initialize(self.directory)
        with connect(self.directory) as db:
            operation = db.execute("SELECT operator_id,operator_name FROM operation WHERE id='old-payment'").fetchone()
            self.assertEqual((operation["operator_id"], operation["operator_name"]), (1, "Operador demo"))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(pos.ticket(self.directory, "old-payment", 1)["amount"], 1000)
        self.assertEqual(pos.batch_summary(self.directory)["net"], 1000)


if __name__ == "__main__":
    unittest.main()
