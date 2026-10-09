# Terminal POS simulado

Simulación educativa de una terminal de comercio para cobros con tarjetas de débito, crédito y QR. La pantalla permite ingresar un importe, presentar una tarjeta ficticia al lector sin contacto o a la ranura del chip, responder un PIN cuando el emisor simulado lo solicita, ver el resultado y emitir un ticket. También incluye acceso de empleados, lote y «Mis cobros».

**No se conecta con Dinelco, bancos ni redes de tarjetas. No mueve dinero real.** El diseño visual es genérico: no reproduce el software ni el comportamiento certificado de un equipo Dinelco. El [diagrama de procesos](docs/diagrama-procesos-terminal.md) se abre en la vista previa Markdown de VS Code con **Ctrl+Shift+V**.

## Requisitos y ejecución

Python 3.10 o posterior y Git. En Windows PowerShell:

```powershell
git clone https://github.com/Alexander-029/pos-cobranza.git
cd pos-cobranza
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m terminal_pos.api --mobile-host 127.0.0.1
```

Si `.venv` ya existe, no hace falta crearlo otra vez. Si le falta `pip`, ejecutá `.\.venv\Scripts\python.exe -m ensurepip --upgrade` y repetí la instalación. Dejá abierta la consola y abrí **http://127.0.0.1:8875/**. Las bases SQLite se crean automáticamente en `terminal_pos/data/` y están excluidas de Git.

El QR con `--mobile-host 127.0.0.1` se puede probar en el mismo equipo. Para confirmarlo desde un celular en la misma red, usá la IPv4 Wi-Fi de la PC en `--mobile-host`; la página móvil escuchará en `TU_IP:8876`, mientras la pantalla del POS seguirá en `127.0.0.1:8875`. No expongas estos puertos a Internet.

## Probar un cobro

1. Ingresá con el empleado `1` y PIN `111111`.
2. Abrí un lote en **Lote**, volvé a **Venta** e ingresá Gs. 1.000.
3. Elegí **Tarjeta** y arrastrá la tarjeta de débito al lector superior. Si arrastrar no funciona, elegí la tarjeta y tocá el lector; también admite Enter/Espacio. Para chip, presentala en la ranura inferior e ingresá el PIN ficticio `1234` cuando se solicite.
4. Una aprobación muestra el papel simulado y habilita el ticket. «Mis cobros» contiene solo las operaciones del empleado autenticado. El lote reúne las operaciones de la terminal y solo quien lo abrió puede cerrarlo.

El empleado `2` usa PIN `222222` y permite comprobar el aislamiento del historial. El PIN de acceso del empleado es distinto del PIN de la tarjeta.

| Tarjeta ficticia | Tipo | Disponible inicial | PIN de tarjeta | Caso de prueba |
|---|---|---:|---:|---|
| `DEB-001` | Débito | Gs. 150.000 | `1234` | Sin contacto pide PIN desde Gs. 100.000 en este perfil. |
| `DEB-002` | Débito | Gs. 80.000 | `4321` | Pide insertar chip después de intentar sin contacto. |
| `CRE-001` | Crédito | Gs. 200.000 | `2468` | Contado o tres cuotas. |
| `CRE-002` | Crédito | Gs. 50.000 | `1357` | Sin contacto pide PIN para cualquier importe. |

Los saldos, límites y reglas de PIN son datos ficticios. Las zonas de lectura se inspiraron en las guías oficiales del [PAX A920 Pro](https://www.pax.us/support/documents/a920-pro-quick-setup-guide/) y del [Ingenico Move 5000](https://ingenico.com/sites/default/files/resource-document/2022-10/MOVE5000%20-%20user%20guide%20-%20OCT22.pdf); no se verificó un manual del modelo Dinelco de la imagen de referencia.

## Estructura

- `terminal_pos/index.html`, `style.css`, `app.js`: vista del terminal e interacción. La animación no autoriza pagos.
- `terminal_pos/api.py`: controlador HTTP; `auth.py`: acceso de empleados; `service.py`: reglas de cobro; `db.py`: esquema y datos iniciales.
- `terminal_pos/data/terminal.db`: empleados, sesiones, lotes, operaciones y QR. `terminal_pos/data/emisor_simulado.db`: cuentas y tarjetas de prueba. Las autorizaciones usan una transacción SQLite con ambas bases para evitar un doble débito.

Los PIN no se guardan en claro y solo se conservan los últimos cuatro dígitos ficticios de la tarjeta. El ticket se reconstruye desde la operación persistida. Las credenciales de prueba son públicas y el servidor usa HTTP local: **no es un sistema de pagos con seguridad de producción**.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest test_terminal_pos.py test_terminal_api.py
```

Las pruebas crean bases temporales; no modifican `terminal_pos/data/`.
