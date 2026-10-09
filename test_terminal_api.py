"""Contrato HTTP sin abrir puertos: ejercita los handlers con streams en memoria."""

from io import BytesIO
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

from terminal_pos.api import make_handlers
from terminal_pos.db import connect, initialize


def call(handler_type, method, path, body=None, content_length=None, cookie=None, content_type="application/json"):
    raw_body = json.dumps(body).encode() if body is not None else b""
    handler = object.__new__(handler_type)
    handler.path = path
    handler.command = method
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.headers = {"Content-Length": str(len(raw_body)) if content_length is None else content_length,
                       "Content-Type": content_type,
                       "Cookie": handler_type.test_cookie if cookie is None and hasattr(handler_type, "test_cookie") else cookie or ""}
    handler.rfile = BytesIO(raw_body)
    handler.wfile = BytesIO()
    getattr(handler, "do_" + method)()
    head, payload = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
    code = int(head.split(b" ", 2)[1])
    if path == "/api/auth/login" and code == 200:
        handler_type.test_cookie = next(line.split(b": ", 1)[1].split(b";", 1)[0].decode()
                                        for line in head.split(b"\r\n") if line.startswith(b"Set-Cookie: "))
    if b"application/json" in head:
        payload = json.loads(payload)
    return code, payload


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=".")
        initialize(self.tmp.name)
        self.desktop, self.mobile = make_handlers(self.tmp.name, "127.0.0.1", 8876)
        self.assertEqual(call(self.desktop, "POST", "/api/auth/login", {"code": "1", "pin": "111111"})[0], 200)

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_card_api_and_basic_page(self):
        status, page = call(self.desktop, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Simulador de terminal POS", page)
        self.assertIn(b'id="screenBatch"', page)
        self.assertIn(b'id="screenHistory"', page)
        self.assertIn(b'data-card="DEB-001"', page)
        self.assertIn(b'data-card="CRE-001"', page)
        self.assertIn(b'id="contactlessTarget"', page)
        self.assertIn(b'id="chipTarget"', page)
        self.assertIn(b'id="screenPin"', page)
        self.assertIn(b'id="screenLogin"', page)
        self.assertIn(b'id="pinKeypad"', page)
        self.assertIn(b".pos-device", call(self.desktop, "GET", "/style.css")[1])
        self.assertIn(b"animateCardRead", call(self.desktop, "GET", "/app.js")[1])
        self.assertIn(b"DEB-001", call(self.desktop, "GET", "/README.md")[1])
        self.assertEqual(call(self.desktop, "POST", "/api/batch/open", {})[0], 200)
        status, pending = call(self.desktop, "POST", "/api/card/start",
                               {"request_id": "web-1", "amount": 100, "card_id": "DEB-001"})
        self.assertEqual((status, pending["status"]), (200, "PENDING"))
        status, result = call(self.desktop, "POST", "/api/card/submit",
                              {"request_id": "web-1", "read_method": "CONTACTLESS"})
        self.assertEqual((status, result["status"]), (200, "APPROVED"))
        self.assertEqual(call(self.desktop, "GET", "/api/ticket/" + result["id"])[1]["amount"], 100)
        self.assertEqual(call(self.desktop, "GET", "/api/batch")[1]["net"], 100)

    def test_cancel_pending_card_through_api(self):
        call(self.desktop, "POST", "/api/batch/open", {})
        call(self.desktop, "POST", "/api/card/start",
             {"request_id": "cancel-web", "amount": 120_000, "card_id": "DEB-001"})
        self.assertEqual(call(self.desktop, "POST", "/api/card/submit",
                              {"request_id": "cancel-web", "read_method": "CONTACTLESS"})[1]["action"],
                         "PIN_REQUIRED")
        status, cancelled = call(self.desktop, "POST", "/api/card/cancel", {"request_id": "cancel-web"})
        self.assertEqual((status, cancelled["status"]), (200, "CANCELLED"))
        self.assertEqual(call(self.desktop, "GET", "/api/batch")[1]["net"], 0)

    def test_invalid_content_length_returns_json_error(self):
        status, body = call(self.desktop, "POST", "/api/batch/open", {},
                            content_length="not-a-number")
        self.assertEqual((status, body["error"]), (400, "INVALID_BODY"))

    def test_browser_form_content_type_cannot_submit_payment(self):
        status, body = call(self.desktop, "POST", "/api/batch/open", {}, content_type="text/plain")
        self.assertEqual((status, body["error"]), (415, "INVALID_CONTENT_TYPE"))
        self.assertEqual(call(self.desktop, "POST", "/api/batch/open", {},
                              content_type="application/json; charset=utf-8")[0], 200)

    def test_missing_qr_library_does_not_leave_pending_operation(self):
        call(self.desktop, "POST", "/api/batch/open", {})
        with patch.dict(sys.modules, {"segno": None}):
            status, body = call(self.desktop, "POST", "/api/qr/start",
                                {"request_id": "missing-library", "amount": 100})
        self.assertEqual((status, body["error"]), (503, "QR_DEPENDENCY_MISSING"))
        self.assertEqual(call(self.desktop, "GET", "/api/operations")[1], [])

    def test_mobile_qr_confirms_same_operation_once(self):
        call(self.desktop, "POST", "/api/batch/open", {})
        status, qr = call(self.desktop, "POST", "/api/qr/start", {"request_id": "qr-web", "amount": 200})
        self.assertEqual(status, 200)
        self.assertIn("<svg", qr["svg"])
        self.assertIn(qr["token"], qr["url"])
        self.assertIn(b"Gs. 200", call(self.mobile, "GET", "/qr/" + qr["token"])[1])
        self.assertEqual(call(self.mobile, "POST", "/qr/" + qr["token"])[0], 200)
        self.assertEqual(call(self.mobile, "POST", "/qr/" + qr["token"])[0], 200)
        status, result = call(self.desktop, "GET", "/api/qr/status?request_id=qr-web")
        self.assertEqual((status, result["status"]), (200, "APPROVED"))
        self.assertEqual(call(self.desktop, "GET", "/api/batch")[1]["net"], 200)

    def test_login_is_required_and_operations_belong_to_employee(self):
        self.assertEqual(call(self.desktop, "GET", "/api/operations", cookie="wrong=token")[0], 401)
        self.assertEqual(call(self.desktop, "POST", "/api/batch/open", {}, cookie="wrong=token")[0], 401)
        call(self.desktop, "POST", "/api/batch/open", {"operator_id": 2})
        first_cookie = self.desktop.test_cookie
        _, first = call(self.desktop, "POST", "/api/card/start",
                        {"request_id": "employee-one", "amount": 100, "card_id": "DEB-001"})
        _, first = call(self.desktop, "POST", "/api/card/submit",
                        {"request_id": "employee-one", "read_method": "CONTACTLESS"})
        self.assertEqual(first["operator_id"], 1)
        self.assertEqual(call(self.desktop, "POST", "/api/auth/login", {"code": "2", "pin": "222222"})[0], 200)
        second_cookie = self.desktop.test_cookie
        self.assertEqual(call(self.desktop, "GET", "/api/operations")[1], [])
        self.assertEqual(call(self.desktop, "GET", "/api/ticket/" + first["id"])[0], 404)
        self.assertEqual(call(self.desktop, "GET", "/api/operation/" + first["id"])[0], 404)
        self.assertEqual(call(self.desktop, "POST", "/api/void", {"request_id": "void-other", "original_id": first["id"]})[0], 409)
        _, second = call(self.desktop, "POST", "/api/card/start",
                         {"request_id": "employee-two", "amount": 200, "card_id": "DEB-001"})
        self.assertEqual(second["operator_id"], 2)
        _, second = call(self.desktop, "POST", "/api/card/submit",
                         {"request_id": "employee-two", "read_method": "CONTACTLESS"})
        self.assertEqual(call(self.desktop, "GET", "/api/ticket/" + second["id"])[1]["operator"], "Operador dos")
        self.assertEqual([op["id"] for op in call(self.desktop, "GET", "/api/operations")[1]], [second["id"]])
        self.desktop.test_cookie = first_cookie
        self.assertEqual([op["id"] for op in call(self.desktop, "GET", "/api/operations")[1]], [first["id"]])
        self.assertEqual(call(self.desktop, "GET", "/api/ticket/" + first["id"])[1]["operator"], "Operador demo")
        self.desktop.test_cookie = second_cookie

    def test_logout_and_failed_pin_lockout(self):
        self.assertEqual(call(self.desktop, "POST", "/api/auth/logout", {})[0], 200)
        self.assertEqual(call(self.desktop, "GET", "/api/auth/me")[0], 401)
        for _ in range(4):
            self.assertEqual(call(self.desktop, "POST", "/api/auth/login", {"code": "2", "pin": "000000"})[0], 401)
        self.assertEqual(call(self.desktop, "POST", "/api/auth/login", {"code": "2", "pin": "000000"})[0], 429)
        self.assertEqual(call(self.desktop, "POST", "/api/auth/login", {"code": "2", "pin": "222222"})[0], 429)

    def test_employee_cannot_complete_another_employees_pending_payment(self):
        call(self.desktop, "POST", "/api/batch/open", {})
        _, card = call(self.desktop, "POST", "/api/card/start",
                       {"request_id": "private-card", "amount": 100, "card_id": "DEB-001"})
        _, qr = call(self.desktop, "POST", "/api/qr/start", {"request_id": "private-qr", "amount": 200})
        call(self.desktop, "POST", "/api/auth/login", {"code": "2", "pin": "222222"})
        self.assertEqual(call(self.desktop, "POST", "/api/card/submit",
                              {"request_id": "private-card", "read_method": "CONTACTLESS"})[0], 404)
        self.assertEqual(call(self.desktop, "POST", "/api/card/cancel", {"request_id": "private-card"})[0], 404)
        self.assertEqual(call(self.desktop, "GET", "/api/qr/status?request_id=private-qr")[0], 404)
        self.assertEqual(call(self.desktop, "POST", "/api/qr/cancel", {"request_id": "private-qr"})[0], 404)
        self.assertEqual(call(self.desktop, "POST", "/api/card/start",
                              {"request_id": "private-card", "amount": 100, "card_id": "DEB-001"})[0], 404)
        self.assertEqual(call(self.mobile, "POST", "/qr/" + qr["token"])[0], 200)
        self.assertEqual(call(self.desktop, "GET", "/api/ticket/" + card["id"])[0], 404)

    def test_only_batch_opener_can_close_and_pending_sales_are_cancelled(self):
        _, batch = call(self.desktop, "POST", "/api/batch/open", {})
        first_cookie = self.desktop.test_cookie
        call(self.desktop, "POST", "/api/auth/login", {"code": "2", "pin": "222222"})
        _, pending = call(self.desktop, "POST", "/api/card/start",
                          {"request_id": "second-pending", "amount": 100, "card_id": "DEB-001"})
        self.assertEqual(pending["batch_id"], batch["id"])
        self.assertEqual(call(self.desktop, "POST", "/api/batch/close", {})[0], 403)
        self.assertEqual(call(self.desktop, "GET", "/api/batch")[1]["batch"]["closed_at"], None)
        self.desktop.test_cookie = first_cookie
        self.assertEqual(call(self.desktop, "POST", "/api/batch/close", {})[0], 200)
        self.assertEqual(call(self.desktop, "GET", "/api/batch")[1]["net"], 0)
        with connect(self.tmp.name) as db:
            self.assertEqual(db.execute("SELECT status FROM operation WHERE id=?", (pending["id"],)).fetchone()[0], "CANCELLED")

    def test_expired_session_and_non_ascii_code_are_rejected(self):
        self.assertEqual(call(self.desktop, "POST", "/api/auth/login", {"code": "²", "pin": "111111"})[0], 401)
        self.assertEqual(call(self.desktop, "POST", "/api/auth/login", {"code": 1, "pin": "111111"})[0], 401)
        with connect(self.tmp.name) as db:
            db.execute("UPDATE operator_session SET expires_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(call(self.desktop, "GET", "/api/auth/me")[0], 401)
        self.assertEqual(call(self.desktop, "POST", "/api/auth/login", {"code": "1", "pin": "111111"})[0], 200)
        with connect(self.tmp.name) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM operator_session").fetchone()[0], 1)

    def test_payment_endpoints_require_session_and_secrets_are_not_plaintext(self):
        for path in ("/api/batch", "/api/cards-demo", "/api/operations", "/api/ticket/unknown"):
            self.assertEqual(call(self.desktop, "GET", path, cookie="pos_session=invalid")[0], 401)
        for path, payload in (
            ("/api/batch/open", {}),
            ("/api/card/start", {"request_id": "guess-card", "amount": 1, "card_id": "DEB-001"}),
            ("/api/qr/start", {"request_id": "guess-qr", "amount": 1}),
            ("/api/void", {"request_id": "guess-void", "original_id": "unknown"}),
        ):
            self.assertEqual(call(self.desktop, "POST", path, payload, cookie="pos_session=invalid")[0], 401)
        with connect(self.tmp.name) as db:
            self.assertNotEqual(db.execute("SELECT pin_digest FROM operator WHERE id=1").fetchone()[0], "111111")
            token = self.desktop.test_cookie.split("=", 1)[1]
            digest = db.execute("SELECT token_digest FROM operator_session").fetchone()[0]
            self.assertNotEqual(digest, token)
            self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
