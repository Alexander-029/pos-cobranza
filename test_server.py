import tempfile
import unittest
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import server


class CollectionTests(unittest.TestCase):
    def setUp(self):
        temp_root = server.ROOT / ".test-temp"
        temp_root.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=temp_root)
        server.DATA = Path(self.folder.name)
        server.SESSIONS.clear()
        server.MOBILE_BASE = "http://127.0.0.1:8766"
        server.init_db()

    def tearDown(self):
        self.folder.cleanup()

    def test_provider_account_isolation_and_no_debt(self):
        ande = server.lookup("ANDE", "DEMO-ANDE-001")
        essap = server.lookup("ESSAP", "DEMO-ESSAP-001")
        self.assertEqual(ande["account"]["titular"], essap["account"]["titular"])
        self.assertTrue(all(i["id"].startswith("A-") for i in ande["invoices"]))
        self.assertTrue(all(i["id"].startswith("E-") for i in essap["invoices"]))
        self.assertEqual(server.lookup("ANDE", "DEMO-ANDE-000")["status"], "SIN_DEUDA")
        with self.assertRaises(server.BusinessError):
            server.lookup("ESSAP", "DEMO-ANDE-001")

    def test_payment_is_exact_applied_once_and_visible_to_its_employee(self):
        server.open_cash(1)
        payload = {"provider": "ANDE", "reference": "DEMO-ANDE-001",
                   "invoice_ids": ["A-2026-08", "A-2026-09"], "request_id": "test-one"}
        payment = server.charge(payload)
        self.assertEqual(payment["importe"], 247000)
        self.assertEqual(len(payment["applications"]), 2)
        self.assertEqual(server.charge(payload)["id"], payment["id"])
        self.assertEqual(len(server.pos_history(1)), 1)
        self.assertEqual(server.pos_history(2), [])
        self.assertNotIn("provider_history", server.lookup("ANDE", "DEMO-ANDE-001"))
        self.assertEqual(server.lookup("ANDE", "DEMO-ANDE-001")["status"], "SIN_DEUDA")
        with self.assertRaises(server.BusinessError):
            server.charge({**payload, "request_id": "test-two"})
        with self.assertRaises(server.BusinessError):
            server.charge({**payload, "invoice_ids": ["A-2026-08"]})
        self.assertEqual(server.close_cash()["total"], 247000)
        with self.assertRaises(server.BusinessError):
            server.charge({"provider": "ESSAP", "reference": "DEMO-ESSAP-001",
                           "invoice_ids": ["E-2026-09"], "request_id": "test-three"})

    def test_invoice_cannot_be_charged_against_another_provider(self):
        server.open_cash(2)
        with self.assertRaises(server.BusinessError):
            server.charge({"provider": "ESSAP", "reference": "DEMO-ESSAP-001",
                           "invoice_ids": ["A-2026-08"], "request_id": "wrong-provider"})
        self.assertEqual(server.pos_history(2), [])
        self.assertEqual(server.lookup("ANDE", "DEMO-ANDE-001")["status"], "PENDIENTE")

    def test_demo_login_and_cash_owner(self):
        with self.assertRaises(server.BusinessError):
            server.login(1, "0000")
        token, employee = server.login(1, "1234")
        self.assertEqual(employee["nombre"], "Lucía Benítez")
        self.assertEqual(server.session_employee(token)["id"], 1)
        server.open_cash(employee["id"])
        with self.assertRaises(server.BusinessError):
            server.logout(token, employee["id"])
        with self.assertRaises(server.BusinessError):
            server.close_cash(2)
        with self.assertRaises(server.BusinessError):
            server.charge({"provider": "ESSAP", "reference": "DEMO-ESSAP-001",
                           "invoice_ids": ["E-2026-09"], "request_id": "other-employee"}, 2)
        server.close_cash(employee["id"])
        server.logout(token, employee["id"])
        self.assertIsNone(server.session_employee(token))

    def test_old_employee_table_is_migrated_without_losing_row(self):
        legacy = Path(self.folder.name) / "legacy"
        legacy.mkdir()
        with closing(sqlite3.connect(legacy / "pos.db")) as db:
            with db:
                db.execute("CREATE TABLE empleado(id INTEGER PRIMARY KEY,nombre TEXT NOT NULL)")
                db.execute("INSERT INTO empleado VALUES (1,'Lucía Benítez')")
        server.DATA = legacy
        server.init_db()
        self.assertEqual(server.employees()[0]["nombre"], "Lucía Benítez")
        self.assertEqual(server.login(1, "1234")[1]["id"], 1)

    def test_qr_from_phone_confirms_once_and_is_scoped_to_cashier(self):
        server.open_cash(1)
        request = server.start_qr({"provider": "ESSAP", "reference": "DEMO-ESSAP-001",
                                   "invoice_ids": ["E-2026-09"]}, 1)
        token = request["url"].split("/")[-1]
        self.assertTrue(request["svg"].startswith("PD94bWw"))
        self.assertEqual(server.mobile_qr(token)["amount"], 64000)
        self.assertEqual(server.qr_status(request["id"], 1)["status"], "PENDIENTE")
        with self.assertRaises(server.BusinessError):
            server.qr_status(request["id"], 2)
        with self.assertRaises(server.BusinessError):
            server.charge({"provider": "ESSAP", "reference": "DEMO-ESSAP-001",
                           "invoice_ids": ["E-2026-09"], "request_id": request["id"], "medio": "QR"}, 1)
        server.confirm_mobile_qr(token)
        first = server.qr_status(request["id"], 1)
        self.assertEqual(first["status"], "CONFIRMADA")
        self.assertEqual(first["receipt"]["medio"], "QR")
        server.confirm_mobile_qr(token)
        self.assertEqual(len(server.pos_history(1)), 1)
        self.assertEqual(server.pos_history(2), [])
        self.assertEqual(server.lookup("ESSAP", "DEMO-ESSAP-001")["status"], "SIN_DEUDA")
        self.assertEqual(server.close_cash(1)["by_method"], {"QR": 64000})

    def test_expired_and_cancelled_qr_never_charge(self):
        server.open_cash(1)
        payload = {"provider": "TIGO", "reference": "DEMO-TIGO-001", "invoice_ids": ["T-2026-09"]}
        expired = server.start_qr(payload, 1)
        with server.connect() as db:
            db.execute("UPDATE qr_solicitud SET expira_en=? WHERE id=?", (int(time.time()) - 1, expired["id"]))
        with self.assertRaises(server.BusinessError):
            server.confirm_mobile_qr(expired["url"].split("/")[-1])
        self.assertEqual(server.qr_status(expired["id"], 1)["status"], "CANCELADA")
        cancelled = server.start_qr(payload, 1)
        self.assertEqual(server.cancel_qr(cancelled["id"], 1)["status"], "CANCELADA")
        with self.assertRaises(server.BusinessError):
            server.confirm_mobile_qr(cancelled["url"].split("/")[-1])
        self.assertEqual(server.lookup("TIGO", "DEMO-TIGO-001")["status"], "PENDIENTE")

    def test_qr_recovery_keeps_confirmed_payment_if_response_was_lost(self):
        server.open_cash(1)
        request = server.start_qr({"provider": "ESSAP", "reference": "DEMO-ESSAP-001",
                                   "invoice_ids": ["E-2026-09"]}, 1)
        # Simula una caída entre guardar el cobro y actualizar la solicitud QR.
        payment = server.charge({"provider": "ESSAP", "reference": "DEMO-ESSAP-001",
                                 "invoice_ids": ["E-2026-09"], "request_id": request["id"],
                                 "medio": "QR"}, 1, allow_qr=True)
        self.assertEqual(server.cancel_qr(request["id"], 1)["status"], "CONFIRMADA")
        self.assertEqual(server.qr_status(request["id"], 1)["receipt"]["id"], payment["id"])
        self.assertEqual(len(server.pos_history(1)), 1)

    def test_old_cash_payment_table_migrates_without_losing_payment(self):
        legacy = Path(self.folder.name) / "old-payments"
        legacy.mkdir()
        with closing(sqlite3.connect(legacy / "pos.db")) as db:
            db.executescript("""
                CREATE TABLE empleado(id INTEGER PRIMARY KEY,nombre TEXT NOT NULL);
                INSERT INTO empleado VALUES (1,'Lucía Benítez');
                CREATE TABLE caja(id INTEGER PRIMARY KEY,empleado_id INTEGER NOT NULL REFERENCES empleado(id),abierta_en TEXT NOT NULL,cerrada_en TEXT);
                INSERT INTO caja VALUES (1,1,'2026-10-01T00:00:00+00:00','2026-10-01T01:00:00+00:00');
                CREATE TABLE cobro(id TEXT PRIMARY KEY,solicitud_id TEXT NOT NULL UNIQUE,caja_id INTEGER NOT NULL REFERENCES caja(id),prestadora TEXT NOT NULL,referencia TEXT NOT NULL,titular TEXT NOT NULL,importe INTEGER NOT NULL CHECK(importe > 0),medio TEXT NOT NULL CHECK(medio = 'EFECTIVO'),confirmado_en TEXT NOT NULL);
                INSERT INTO cobro VALUES ('old-1','old-request',1,'ANDE','DEMO-ANDE-001','María González',1000,'EFECTIVO','2026-10-01T00:10:00+00:00');
                CREATE TABLE aplicacion(cobro_id TEXT NOT NULL REFERENCES cobro(id),factura_id TEXT NOT NULL,importe INTEGER NOT NULL CHECK(importe > 0),PRIMARY KEY(cobro_id,factura_id));
                INSERT INTO aplicacion VALUES ('old-1','A-2026-07',1000);
            """)
        server.DATA = legacy
        server.init_db()
        with server.connect() as db:
            self.assertEqual(db.execute("SELECT medio FROM cobro WHERE id='old-1'").fetchone()[0], "EFECTIVO")
            self.assertEqual(db.execute("SELECT factura_id FROM aplicacion WHERE cobro_id='old-1'").fetchone()[0], "A-2026-07")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
