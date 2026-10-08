import tempfile
import unittest
import sqlite3
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

    def test_payment_is_exact_applied_once_and_visible_in_both_histories(self):
        server.open_cash(1)
        payload = {"provider": "ANDE", "reference": "DEMO-ANDE-001",
                   "invoice_ids": ["A-2026-08", "A-2026-09"], "request_id": "test-one"}
        payment = server.charge(payload)
        self.assertEqual(payment["importe"], 247000)
        self.assertEqual(len(payment["applications"]), 2)
        self.assertEqual(server.charge(payload)["id"], payment["id"])
        self.assertEqual(len(server.pos_history()), 1)
        self.assertEqual(len(server.lookup("ANDE", "DEMO-ANDE-001")["provider_history"]), 2)
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
        self.assertEqual(server.pos_history(), [])
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


if __name__ == "__main__":
    unittest.main()
