# Verificación del terminal POS simulado

Fecha: 8 de octubre de 2026. Alcance: `terminal_pos/`, sus dos bases SQLite y su API local. El cobrador de facturas anterior (`server.py`, puerto 8765) no cambió.

## Resultados ejecutados

| Comprobación | Resultado |
|---|---|
| `python -m unittest test_terminal_pos.py test_terminal_api.py` | 31 pruebas aprobadas |
| `node --check terminal_pos/app.js` | Sintaxis válida |
| `git diff --check` | Sin errores de formato |
| Bases locales: `PRAGMA quick_check` | `ok` en `terminal.db` y `emisor_simulado.db` |
| Bases locales: `PRAGMA foreign_key_check` | Sin violaciones en ambas bases |
| Base local del emisor: saldos negativos | 0 |
| Ventas y anulaciones paralelas | Tres repeticiones adicionales sin doble débito ni doble devolución |

Las pruebas comprueban importes mínimo/máximo, fondos insuficientes, PIN de tarjeta, bloqueo por intentos, idempotencia, QR confirmado/cancelado/vencido, anulación, lote abierto/cerrado, migración de un pago anterior, sesiones vencidas, acceso entre empleados y solicitudes sin autenticación. Ocho ventas simultáneas de Gs. 30.000 contra Gs. 150.000 aprobaron exactamente cinco y rechazaron tres; seis anulaciones simultáneas devolvieron fondos una sola vez.

## Correcciones de esta ronda

- Solo el empleado que abrió el lote puede cerrarlo; los cobros de otros empleados siguen asociados a ellos.
- El código y PIN de acceso aceptan únicamente dígitos ASCII y las sesiones vencidas se limpian al iniciar sesión.
- La API de pagos exige `Content-Type: application/json`, para rechazar formularios de otro origen enviados como texto simple.
- Si falta `segno`, la creación del QR devuelve un error explícito antes de guardar una operación pendiente.
- Para equipos donde arrastrar la tarjeta falla, se puede elegirla y tocar el lector superior o la ranura inferior, también con teclado.

## Límites de la verificación

No se ejecutó una prueba visual automatizada: el control de navegador rechazó acceder a `127.0.0.1:8875` y prohibió repetir esa acción mediante Playwright u otra superficie. Por lo tanto, el recorrido de pantalla aún requiere comprobación manual antes de afirmar que el desarrollo está terminado visualmente. Tampoco se repitió una auditoría de dependencias: `pip-audit` no está instalado en este entorno. No se agregó ninguna dependencia en esta ronda.

Prueba manual mínima pendiente: recargar la página; ingresar con empleado `1` y PIN `111111`; abrir lote; marcar Gs. 1.000; elegir débito; arrastrar la tarjeta al lector superior o elegirla y tocarlo; comprobar que aparezcan aprobación y ticket, y que «Mis cobros» registre la operación. Repetir con chip y PIN ficticio `1234`, crédito y QR.
