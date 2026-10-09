# Terminal POS simulado

Este módulo es **independiente** del cobrador de facturas de la raíz del repositorio. Simula una terminal de comercio para débito, crédito y QR. No se conecta con Dinelco, bancos ni redes de tarjetas; los saldos, PIN, autorizaciones y tickets son ficticios.

## Ejecutar

Desde la raíz del repositorio:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -u -m terminal_pos.api --mobile-host 127.0.0.1
```

Dejá esa terminal abierta mientras uses el simulador y abrí `http://127.0.0.1:8875/` en el navegador. Si todavía no existe `.venv`, crealo una vez con `python -m venv .venv`. La base se crea en `terminal_pos/data/` la primera vez. Para probar el QR en un teléfono de la misma red, reemplazá `127.0.0.1` en `--mobile-host` por la IPv4 Wi-Fi de este equipo. La página móvil escucha en `TU_IP_WIFI:8876`; el POS queda exclusivamente en `127.0.0.1:8875`. Si el firewall o la red impide entrar, el QR se puede probar desde el navegador del mismo equipo usando `127.0.0.1`. No expongas el puerto móvil a Internet.

La operación se hace en la pantalla del POS: iniciá sesión con un empleado de prueba, entrá en **Lote** y abrilo; volvé a **Venta**, ingresá el importe con el teclado y elegí tarjeta o QR. Para tarjeta, elegí una de las dos tarjetas ficticias del costado y arrastrala al lector superior (sin contacto) o a la ranura inferior (chip). El plan de tres cuotas está disponible en la tarjeta de crédito de prueba: seleccionála, elegí el plan y después arrastrala. Si el emisor simulado pide PIN, aparece una pantalla independiente con teclado y cuatro indicadores enmascarados. **Mis cobros** muestra solo las operaciones del empleado que inició sesión; permite reabrir su ticket y anular una venta propia del lote abierto. El movimiento de la tarjeta **no** autoriza la venta. El papel aparece solo después de una aprobación persistida. El resumen del lote muestra el total de la terminal, incluso si cobraron dos empleados; cerrar el lote no deposita fondos. Las posiciones de los lectores se fundamentan en [manuales oficiales de equipos comparables](../docs/lectores-terminal-pos.md), no en un manual verificado del Dinelco de la foto.

Si se abandona una venta mientras pide chip o PIN, el botón **Cancelar esta venta** deja su operación en estado cancelado. Una confirmación tardía no la puede aprobar ni descontar saldo ficticio.

## Datos de prueba

| Empleado ficticio | Código | PIN de acceso |
|---|---:|---:|
| Operador demo | `1` | `111111` |
| Operador dos | `2` | `222222` |

Son credenciales públicas solo para esta simulación local. El PIN de acceso tiene seis dígitos y es **distinto** del PIN ficticio de la tarjeta. Después de cinco errores consecutivos, ese empleado queda bloqueado quince minutos. La sesión dura ocho horas o hasta tocar **Salir**. Para comprobar el aislamiento, cobrá con el empleado 1, salí e ingresá con el 2: la pantalla «Mis cobros» estará vacía para el segundo empleado.

| Tarjeta ficticia | Tipo | Disponible inicial | PIN ficticio | Particularidad |
|---|---|---:|---:|---|
| `DEB-001` | Débito | Gs. 150.000 | `1234` | Sin contacto pide PIN desde Gs. 100.000 **solo en este perfil de prueba**. |
| `DEB-002` | Débito | Gs. 80.000 | `4321` | Pide cambiar de sin contacto a chip. |
| `CRE-001` | Crédito | Gs. 200.000 | `2468` | Contado o tres cuotas; sin contacto sin PIN. |
| `CRE-002` | Crédito | Gs. 50.000 | `1357` | Sin contacto pide PIN para cualquier importe; solo contado. |

El PIN de chip y banda se pide en todos estos perfiles; la interfaz actual solo presenta NFC y chip, aunque el backend conserva la regla de banda para futuras pruebas. Tres errores consecutivos bloquean **la tarjeta ficticia en la base del emisor**. Estos valores enseñan que el PIN depende del perfil y del tipo de lectura; no describen reglas universales de una red real. Para reiniciar los datos de la terminal, detené el servidor y eliminá únicamente `terminal_pos/data/terminal.db` y `terminal_pos/data/emisor_simulado.db` si ya no necesitás las operaciones de prueba.

## Modelo y responsabilidades

El [diagrama de datos](../docs/modelo-terminal-pos.md) muestra las relaciones y las reglas que se comprueban en las pruebas.

```text
index.html        Estructura de la vista del terminal.
style.css         Aparato, disposición adaptable y animaciones con movimiento reducido.
app.js            Interacción de la vista y consumo de la API; no autoriza pagos.
api.py            Controlador HTTP. Valida transporte y llama a casos de uso.
service.py        Reglas: lote, lectura, PIN, fondos, QR, ticket y anulación.
auth.py           Verifica PIN de empleado y administra sesiones locales.
db.py             Conexión, tablas y tarjetas iniciales de prueba.
terminal.db       Comercio: operador, terminal, lote, operación, intentos y QR.
emisor_simulado.db Emisor ficticio: cuenta, tarjeta y planes de cuotas.
```

`operation.request_id` es único para que reintentos de una venta no descuenten dos veces. `pin_attempt.attempt_id` evita contar dos veces el mismo intento reenviado. Cada autorización usa una transacción SQLite con ambas bases adjuntas y journal `DELETE`, de modo que el resultado del POS y el saldo ficticio se confirman juntos. El POS no guarda los PIN ni números completos de tarjeta; el PIN del empleado se guarda como hash con sal. El ticket se reconstruye desde la operación persistida; no es una imagen guardada. Cada operación conserva el empleado que la inició, aunque el lote pertenezca a la terminal.

El lote guarda una copia de los nombres del comercio y operador al abrirse, para que una reimpresión conserve esos datos aunque el catálogo cambie después. La inicialización añade esas columnas a las bases de este módulo creadas antes de esa mejora sin borrar operaciones.

La API del POS solo escucha en localhost. La página móvil expone únicamente la solicitud QR identificada por un token temporal. El POS usa una cookie de sesión `HttpOnly` y `SameSite=Strict`; el servidor filtra operaciones y tickets por empleado. La página móvil del QR no necesita la sesión del empleado porque el token solo autoriza confirmar esa solicitud ficticia. No hay integración bancaria ni seguridad de producción: las credenciales son públicas, el servidor local usa HTTP y esta implementación no debe exponerse a Internet.

## API local básica

`POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout`; `POST /api/batch/open`, `POST /api/batch/close`, `GET /api/batch`; `POST /api/card/start`, `POST /api/card/submit`, `POST /api/card/cancel`; `POST /api/qr/start`, `GET /api/qr/status?request_id=…`, `POST /api/qr/cancel`; `POST /api/void`; `GET /api/operations` y `GET /api/ticket/{id}`. Los `POST` usan JSON. Todas las rutas `/api/` salvo acceso requieren sesión. En una venta con tarjeta, primero se crea una operación `PENDING`; el servidor puede responder `INSERT_CARD` o `PIN_REQUIRED` antes de aprobar o rechazar. Una confirmación QR repetida devuelve la misma operación. Un QR vencido o cancelado no suma al lote.

## Verificación

```powershell
.\.venv\Scripts\python.exe -m unittest test_terminal_pos.py test_terminal_api.py
```

Las pruebas crean bases temporales y no modifican `terminal_pos/data/`.
