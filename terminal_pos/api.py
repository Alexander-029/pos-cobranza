"""FastAPI HTTP adapter for the local POS simulator."""
from argparse import ArgumentParser
from html import escape
from pathlib import Path
import threading
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

from . import auth
from . import service as pos
from .db import initialize


class ActionBody(BaseModel):
    """Document the JSON envelope and leave business validation to the domain."""
    model_config = ConfigDict(extra="ignore")
    code: Any = Field(default=None, description="Código de empleado")
    pin: Any = Field(default=None, description="PIN de empleado o tarjeta ficticia")
    request_id: Any = Field(default=None, description="Identificador idempotente de la operación")
    amount: Any = Field(default=None, description="Importe entero en guaraníes")
    card_id: Any = Field(default=None, description="Identificador de tarjeta de demostración")
    read_method: Any = Field(default=None, description="CONTACTLESS, CHIP o MAGSTRIPE")
    installments: Any = Field(default=1, description="Cantidad de cuotas")
    attempt_id: Any = Field(default=None, description="Identificador del intento de PIN")
    original_id: Any = Field(default=None, description="ID de la venta a anular")


def make_app(directory, mobile_host=None, mobile_port=8876):
    directory = Path(directory)
    page = Path(__file__).with_name("index.html")
    stylesheet = Path(__file__).with_name("style.css")
    script = Path(__file__).with_name("app.js")
    guide = Path(__file__).resolve().parent.parent / "README.md"
    app = FastAPI(title="POS Lab API", description="API local del simulador de cobranzas")

    @app.middleware("http")
    async def local_http_policy(request: Request, call_next):
        if request.method == "POST" and request.url.path.startswith("/api/"):
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                response = JSONResponse({"error": "INVALID_CONTENT_TYPE", "message": "Se requiere application/json"}, status_code=415)
            else:
                try:
                    length = int(request.headers.get("content-length", "0"))
                except (TypeError, ValueError):
                    length = -1
                if length < 1 or length > 16_384:
                    response = JSONResponse({"error": "INVALID_BODY", "message": "Contenido JSON inválido"}, status_code=400)
                else:
                    response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    @app.exception_handler(pos.PosError)
    async def pos_error_handler(_request: Request, exc: pos.PosError):
        return JSONResponse({"error": exc.code, "message": str(exc)}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def body_error_handler(_request: Request, exc: RequestValidationError):
        errors = exc.errors()
        non_object = any(error.get("type") in {"dict_type", "model_attributes_type"} for error in errors)
        message = "Se esperaba un objeto JSON" if non_object else "Contenido JSON inválido"
        return JSONResponse({"error": "INVALID_BODY", "message": message}, status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException):
        if exc.status_code == 404:
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        return JSONResponse({"error": "HTTP_ERROR", "message": str(exc.detail)}, status_code=exc.status_code)

    def operator(request: Request):
        return auth.current_operator(directory, request.cookies.get("pos_session"))

    def run(action):
        try:
            return action()
        except pos.PosError as exc:
            return JSONResponse({"error": exc.code, "message": str(exc)}, status_code=exc.status)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(page, media_type="text/html; charset=utf-8")

    @app.get("/style.css", include_in_schema=False)
    def styles():
        return FileResponse(stylesheet, media_type="text/css; charset=utf-8")

    @app.get("/app.js", include_in_schema=False)
    def javascript():
        return FileResponse(script, media_type="text/javascript; charset=utf-8")

    @app.get("/README.md", include_in_schema=False)
    def readme():
        return FileResponse(guide, media_type="text/plain; charset=utf-8")

    @app.get("/api/auth/me", tags=["Autenticación"])
    def auth_me(current=Depends(operator)):
        return current

    @app.post("/api/auth/login", tags=["Autenticación"])
    def login(data: ActionBody = Body(...)):
        try:
            result = auth.login(directory, data.code, data.pin)
            token = result.pop("token")
            response = JSONResponse(result)
            response.set_cookie("pos_session", token, httponly=True, samesite="strict", path="/", max_age=28_800)
            return response
        except pos.PosError as exc:
            return JSONResponse({"error": exc.code, "message": str(exc)}, status_code=exc.status)

    @app.post("/api/auth/logout", tags=["Autenticación"])
    def logout(request: Request, _data: ActionBody = Body(...)):
        auth.logout(directory, request.cookies.get("pos_session"))
        response = JSONResponse({"ok": True})
        response.set_cookie("pos_session", "", httponly=True, samesite="strict", path="/", max_age=0)
        return response

    @app.get("/api/cards-demo", tags=["Terminal"])
    def cards(_current=Depends(operator)):
        return run(lambda: pos.demo_cards(directory))

    @app.get("/api/operations", tags=["Operaciones"])
    def operations(current=Depends(operator)):
        return run(lambda: pos.operations(directory, operator_id=current["id"]))

    @app.get("/api/batch", tags=["Lotes"])
    def batch(current=Depends(operator)):
        return run(lambda: pos.batch_summary(directory))

    @app.get("/api/ticket/{operation_id}", tags=["Operaciones"])
    def ticket(operation_id: str, current=Depends(operator)):
        return run(lambda: pos.ticket(directory, operation_id, current["id"]))

    @app.get("/api/operation/{operation_id}", tags=["Operaciones"])
    def operation(operation_id: str, current=Depends(operator)):
        return run(lambda: pos.operation(directory, operation_id, current["id"]))

    @app.get("/api/qr/status", tags=["QR"])
    def qr_status(request_id: str | None = None, current=Depends(operator)):
        return run(lambda: pos.operation_by_request(directory, request_id, "QR", current["id"]))

    @app.post("/api/batch/open", tags=["Lotes"])
    def batch_open(_data: ActionBody = Body(...), current=Depends(operator)):
        return run(lambda: pos.open_batch(directory, current["id"]))

    @app.post("/api/batch/close", tags=["Lotes"])
    def batch_close(_data: ActionBody = Body(...), current=Depends(operator)):
        return run(lambda: pos.close_batch(directory, current["id"]))

    @app.post("/api/card/start", tags=["Tarjetas"])
    def card_start(data: ActionBody, current=Depends(operator)):
        return run(lambda: pos.start_card(directory, data.request_id, data.amount, data.card_id, current["id"]))

    @app.post("/api/card/submit", tags=["Tarjetas"])
    def card_submit(data: ActionBody, current=Depends(operator)):
        return run(lambda: pos.submit_card(directory, data.request_id, data.read_method, data.installments, data.pin, data.attempt_id, current["id"]))

    @app.post("/api/card/cancel", tags=["Tarjetas"])
    def card_cancel(data: ActionBody, current=Depends(operator)):
        return run(lambda: pos.cancel_card(directory, data.request_id, current["id"]))

    @app.post("/api/qr/cancel", tags=["QR"])
    def qr_cancel(data: ActionBody, current=Depends(operator)):
        return run(lambda: pos.cancel_qr(directory, data.request_id, current["id"]))

    @app.post("/api/void", tags=["Operaciones"])
    def void(data: ActionBody, current=Depends(operator)):
        return run(lambda: pos.void_sale(directory, data.request_id, data.original_id, current["id"]))

    @app.post("/api/qr/start", tags=["QR"])
    def qr_start(data: ActionBody, current=Depends(operator)):
        if mobile_host is None:
            return JSONResponse({"error": "QR_UNAVAILABLE", "message": "Iniciá con --mobile-host para probar QR desde un celular"}, status_code=409)
        try:
            import segno
        except ImportError:
            return JSONResponse({"error": "QR_DEPENDENCY_MISSING", "message": "Instalá requirements.txt para generar el QR"}, status_code=503)
        return run(lambda: _qr_result(directory, data, current["id"], mobile_host, mobile_port, segno))

    return app


def _qr_result(directory, data, operator_id, mobile_host, mobile_port, segno):
    result = pos.start_qr(directory, data.request_id, data.amount, operator_id=operator_id)
    result["url"] = f"http://{mobile_host}:{mobile_port}/qr/{result['token']}"
    result["svg"] = segno.make(result["url"]).svg_inline(scale=4)
    return result


def make_mobile_app(directory):
    directory = Path(directory)
    app = FastAPI(title="POS Lab QR", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    @app.exception_handler(pos.PosError)
    async def mobile_pos_error(_request: Request, exc: pos.PosError):
        return PlainTextResponse(str(exc), status_code=exc.status)

    @app.get("/qr/{token}", response_class=HTMLResponse)
    def qr_payment_page(token: str):
        try:
            data = pos.qr_details(directory, token)
            merchant = escape(data["merchant_name"])
            amount = f"{data['amount']:,}".replace(",", ".")
            state = escape(data["status"])
            expires = escape(data["expires_at"])
            button = '<button type="submit">Confirmar pago simulado</button>' if data["status"] == "PENDING" else ""
            return HTMLResponse(f"""<!doctype html><html lang="es"><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <title>Pago QR simulado</title><style>
            body{{font:18px system-ui;max-width:420px;margin:40px auto;padding:20px;color:#222}}
            button{{padding:16px;width:100%;font-size:18px;background:#573285;color:white;border:0}}
            .note{{border:1px solid #999;padding:12px;background:#eee}}
            </style><h1>Pago QR simulado</h1><p class="note">Sin dinero real</p>
            <p>Comercio: <b>{merchant}</b></p><p>Importe: <b>Gs. {amount}</b></p>
            <p>Estado: {state}</p><p>Vence: {expires} UTC</p>
            <form method="post" action="/qr/{escape(token)}">{button}</form></html>""")
        except pos.PosError as exc:
            return PlainTextResponse(str(exc), status_code=exc.status)

    @app.post("/qr/{token}", response_class=HTMLResponse)
    def qr_confirm(token: str):
        try:
            operation = pos.confirm_qr(directory, token)
            message = "Pago simulado confirmado" if operation["status"] == "APPROVED" else f"Estado: {operation['status']}"
            return HTMLResponse("<!doctype html><html lang='es'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>" f"<h1>{escape(message)}</h1><p>No se movió dinero real.</p></html>")
        except pos.PosError as exc:
            return PlainTextResponse(str(exc), status_code=exc.status)

    return app


def main():
    parser = ArgumentParser(description="Terminal POS educativo")
    parser.add_argument("--data", default=str(Path(__file__).resolve().parent / "data"))
    parser.add_argument("--port", type=int, default=8875)
    parser.add_argument("--mobile-host", help="IPv4 Wi-Fi de este equipo, si se probará QR con un teléfono")
    parser.add_argument("--mobile-port", type=int, default=8876)
    args = parser.parse_args()
    initialize(args.data)
    if args.mobile_host:
        mobile_app = make_mobile_app(args.data)
        threading.Thread(target=uvicorn.run, args=(mobile_app,), kwargs={"host": args.mobile_host, "port": args.mobile_port, "log_level": "error", "access_log": False}, daemon=True).start()
        print(f"QR móvil: http://{args.mobile_host}:{args.mobile_port}")
    app = make_app(args.data, args.mobile_host, args.mobile_port)
    print(f"POS simulado: http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error", access_log=False)


if __name__ == "__main__":
    main()
