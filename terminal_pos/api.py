"""Adaptador HTTP local para el simulador; la lógica vive en service.py."""

from argparse import ArgumentParser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse

from .db import initialize
from . import auth
from . import service as pos


def make_handlers(directory, mobile_host=None, mobile_port=8876):
    directory = Path(directory)
    page = Path(__file__).with_name("index.html")
    stylesheet = Path(__file__).with_name("style.css")
    script = Path(__file__).with_name("app.js")
    guide = Path(__file__).with_name("README.md")

    class JsonHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
            data = (json.dumps(body, ensure_ascii=False).encode("utf-8")
                    if isinstance(body, (dict, list)) else body)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def input(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except (TypeError, ValueError):
                raise pos.PosError("INVALID_BODY", "Contenido JSON inválido") from None
            if length < 1 or length > 16_384:
                raise pos.PosError("INVALID_BODY", "Contenido JSON inválido")
            try:
                body = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                raise pos.PosError("INVALID_BODY", "Contenido JSON inválido") from None
            if not isinstance(body, dict):
                raise pos.PosError("INVALID_BODY", "Se esperaba un objeto JSON")
            return body

        def execute(self, action):
            try:
                result = action()
                self.send(200, result)
            except pos.PosError as exc:
                self.send(exc.status, {"error": exc.code, "message": str(exc)})

    class PosHandler(JsonHandler):
        def session_token(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                return cookie["pos_session"].value if "pos_session" in cookie else None
            except Exception:
                return None

        def operator(self):
            return auth.current_operator(directory, self.session_token())

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/":
                self.send(200, page.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/style.css":
                self.send(200, stylesheet.read_bytes(), "text/css; charset=utf-8")
            elif url.path == "/app.js":
                self.send(200, script.read_bytes(), "text/javascript; charset=utf-8")
            elif url.path == "/README.md":
                self.send(200, guide.read_bytes(), "text/plain; charset=utf-8")
            elif url.path == "/api/auth/me":
                self.execute(self.operator)
            elif not url.path.startswith("/api/"):
                self.send(404, {"error": "NOT_FOUND"})
            else:
                try:
                    operator_id = self.operator()["id"]
                except pos.PosError as exc:
                    self.send(exc.status, {"error": exc.code, "message": str(exc)})
                    return
                self.authorized_get(url, query, operator_id)

        def authorized_get(self, url, query, operator_id):
            if url.path == "/api/cards-demo":
                self.execute(lambda: pos.demo_cards(directory))
            elif url.path == "/api/operations":
                self.execute(lambda: pos.operations(directory, operator_id=operator_id))
            elif url.path == "/api/batch":
                self.execute(lambda: pos.batch_summary(directory))
            elif url.path.startswith("/api/ticket/"):
                self.execute(lambda: pos.ticket(directory, url.path.rsplit("/", 1)[1], operator_id))
            elif url.path.startswith("/api/operation/"):
                self.execute(lambda: pos.operation(directory, url.path.rsplit("/", 1)[1], operator_id))
            elif url.path == "/api/qr/status":
                self.execute(lambda: pos.operation_by_request(directory, query.get("request_id", [None])[0], "QR", operator_id))
            else:
                self.send(404, {"error": "NOT_FOUND"})

        def do_POST(self):
            url = urlparse(self.path)
            try:
                data = self.input()
            except pos.PosError as exc:
                self.send(exc.status, {"error": exc.code, "message": str(exc)})
                return
            if url.path == "/api/auth/login":
                try:
                    result = auth.login(directory, data.get("code"), data.get("pin"))
                    token = result.pop("token")
                    self.send(200, result, headers={"Set-Cookie": f"pos_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800"})
                except pos.PosError as exc:
                    self.send(exc.status, {"error": exc.code, "message": str(exc)})
                return
            if url.path == "/api/auth/logout":
                auth.logout(directory, self.session_token())
                self.send(200, {"ok": True}, headers={"Set-Cookie": "pos_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
                return
            try:
                operator_id = self.operator()["id"]
            except pos.PosError as exc:
                self.send(exc.status, {"error": exc.code, "message": str(exc)})
                return
            routes = {
                "/api/batch/open": lambda: pos.open_batch(directory, operator_id),
                "/api/batch/close": lambda: pos.close_batch(directory),
                "/api/card/start": lambda: pos.start_card(directory, data.get("request_id"), data.get("amount"), data.get("card_id"), operator_id),
                "/api/card/submit": lambda: pos.submit_card(directory, data.get("request_id"), data.get("read_method"),
                     data.get("installments", 1), data.get("pin"), data.get("attempt_id"), operator_id),
                "/api/card/cancel": lambda: pos.cancel_card(directory, data.get("request_id"), operator_id),
                "/api/qr/cancel": lambda: pos.cancel_qr(directory, data.get("request_id"), operator_id),
                "/api/void": lambda: pos.void_sale(directory, data.get("request_id"), data.get("original_id"), operator_id),
            }
            if url.path == "/api/qr/start":
                def start():
                    if mobile_host is None:
                        raise pos.PosError("QR_UNAVAILABLE", "Iniciá con --mobile-host para probar QR desde un celular", 409)
                    result = pos.start_qr(directory, data.get("request_id"), data.get("amount"), operator_id=operator_id)
                    result["url"] = f"http://{mobile_host}:{mobile_port}/qr/{result['token']}"
                    import segno
                    result["svg"] = segno.make(result["url"]).svg_inline(scale=4)
                    return result
                self.execute(start)
            elif url.path in routes:
                self.execute(routes[url.path])
            else:
                self.send(404, {"error": "NOT_FOUND"})

    class MobileHandler(JsonHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if not url.path.startswith("/qr/"):
                self.send(404, b"No encontrado", "text/plain; charset=utf-8")
                return
            token = url.path.removeprefix("/qr/")
            try:
                data = pos.qr_details(directory, token)
                merchant = escape(data["merchant_name"])
                amount = f"{data['amount']:,}".replace(",", ".")
                state = escape(data["status"])
                expires = escape(data["expires_at"])
                button = '<button type="submit">Confirmar pago simulado</button>' if data["status"] == "PENDING" else ""
                html = f"""<!doctype html><html lang="es"><meta charset="utf-8">
                <meta name="viewport" content="width=device-width,initial-scale=1">
                <title>Pago QR simulado</title><style>
                body{{font:18px system-ui;max-width:420px;margin:40px auto;padding:20px;color:#222}}
                button{{padding:16px;width:100%;font-size:18px;background:#573285;color:white;border:0}}
                .note{{border:1px solid #999;padding:12px;background:#eee}}
                </style><h1>Pago QR simulado</h1><p class="note">Sin dinero real</p>
                <p>Comercio: <b>{merchant}</b></p><p>Importe: <b>Gs. {amount}</b></p>
                <p>Estado: {state}</p><p>Vence: {expires} UTC</p>
                <form method="post" action="/qr/{escape(token)}">{button}</form></html>"""
                self.send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            except pos.PosError as exc:
                self.send(exc.status, str(exc).encode("utf-8"), "text/plain; charset=utf-8")

        def do_POST(self):
            url = urlparse(self.path)
            if not url.path.startswith("/qr/"):
                self.send(404, b"No encontrado", "text/plain; charset=utf-8")
                return
            try:
                operation = pos.confirm_qr(directory, url.path.removeprefix("/qr/"))
                message = "Pago simulado confirmado" if operation["status"] == "APPROVED" else f"Estado: {operation['status']}"
                html = f"<!doctype html><html lang='es'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><h1>{escape(message)}</h1><p>No se movió dinero real.</p></html>"
                self.send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            except pos.PosError as exc:
                self.send(exc.status, str(exc).encode("utf-8"), "text/plain; charset=utf-8")

    return PosHandler, MobileHandler


def main():
    parser = ArgumentParser(description="Terminal POS educativo")
    parser.add_argument("--data", default=str(Path(__file__).resolve().parent / "data"))
    parser.add_argument("--port", type=int, default=8875)
    parser.add_argument("--mobile-host", help="IPv4 Wi-Fi de este equipo, si se probará QR con un teléfono")
    parser.add_argument("--mobile-port", type=int, default=8876)
    args = parser.parse_args()
    initialize(args.data)
    desktop, mobile = make_handlers(args.data, args.mobile_host, args.mobile_port)
    if args.mobile_host:
        mobile_server = ThreadingHTTPServer((args.mobile_host, args.mobile_port), mobile)
        threading.Thread(target=mobile_server.serve_forever, daemon=True).start()
        print(f"QR móvil: http://{args.mobile_host}:{args.mobile_port}")
    print(f"POS simulado: http://127.0.0.1:{args.port}")
    ThreadingHTTPServer(("127.0.0.1", args.port), desktop).serve_forever()


if __name__ == "__main__":
    main()
