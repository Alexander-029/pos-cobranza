# POS de cobranzas — MVP educativo

> **Nuevo módulo:** [Terminal POS simulado](terminal_pos/README.md) para pagos ficticios con débito, crédito y QR. Es independiente del cobrador de facturas descrito abajo; la vista representa un terminal físico y sus animaciones, mientras la autorización sigue en el backend.

Simula una boca de cobranzas que consulta facturas de ANDE, ESSAP y Tigo Hogar. La interfaz conserva el estilo sobrio de tablas del prototipo aprobado. **No hay conexión con esas empresas ni movimiento de dinero real.**

## Ejecutar

Requiere Python 3.10 o posterior. Segno genera los códigos QR localmente; se verificó `segno==1.6.6` con `pip-audit` sin vulnerabilidades conocidas.

```powershell
cd C:\ruta\al\pos-cobranza
python -m pip install -r requirements.txt
python server.py --mobile-host TU_IP_WIFI
```

Obtené `TU_IP_WIFI` con `ipconfig` (IPv4 del adaptador Wi-Fi; por ejemplo `10.0.9.176`). El POS del cajero queda en `http://127.0.0.1:8765`; solo la página de confirmación QR escucha en `TU_IP_WIFI:8766`. El teléfono debe estar en la misma red y poder acceder a ese puerto. Si Windows bloquea las conexiones entrantes, un administrador debe autorizar **solo TCP 8766 desde la subred local** para el perfil de red activo. No uses datos reales: esta demostración usa HTTP local y PIN públicos. Sin `--mobile-host`, el POS funciona en modo local y el QR queda deshabilitado. Para reiniciar la simulación, detené el servidor y borrá los dos archivos de `data/` (esto elimina todos los cobros locales).

## Recorrido para probar

1. Iniciá sesión con Lucía Benítez (PIN `1234`) o Diego Rojas (PIN `5678`). Son credenciales públicas **solo para esta demostración**.
2. Abrí el archivo separado [DATOS_DE_PRUEBA.md](DATOS_DE_PRUEBA.md), elegí una referencia ficticia y consultala en el POS. El archivo muestra los datos iniciales; los saldos cambian después de cada cobro.
3. Abrí tu caja, elegí una o más facturas pendientes y un medio. Efectivo y tarjeta son registros simulados; **QR** genera una solicitud temporal para escanear con el celular. La página móvil muestra servicio, referencia, facturas y total; tocá **Confirmar pago simulado**.
4. El POS detecta la confirmación y muestra el comprobante. La factura deja de estar pendiente. **Mis cobros** muestra solo los cobros del empleado que ingresó; no expone pagos de otros locales ni de otros empleados.
5. Cerrá la caja para ver cantidad y total por medio; después podés cerrar sesión. Una solicitud QR vence a los cinco minutos o al cerrar la caja y no puede confirmar dos cobros.

## Modelo y límites

- Separación sencilla tipo MVC: las tablas SQLite son el **modelo**, las funciones y rutas de `server.py` controlan las operaciones, e `index.html` es la **vista** con interacción en JavaScript. No se añade un framework para imponer carpetas vacías.
- `data/prestadoras_simuladas.db`: cuentas, facturas y pagos de las prestadoras **ficticias**. Una cuenta se identifica por `(prestadora, referencia)`. El mismo titular puede tener servicios distintos sin mezclar facturas.
- Cada factura tiene `disponible_desde` (fecha local del servidor). El POS solo muestra y cobra facturas pendientes a partir de esa fecha, incluso si se intenta saltar la pantalla y llamar a la API de cobro o QR. `vencimiento` es independiente: una factura ya disponible puede pagarse antes de vencer. [DATOS_DE_PRUEBA.md](DATOS_DE_PRUEBA.md) incluye un `INSERT` SQL para probar una factura futura. El día 28 es solo el valor elegido para esa prueba, no una regla atribuida a ANDE, ESSAP o Tigo.
- `data/pos.db`: empleados, hashes de sus PIN, cajas, catálogo de medios, cobros, solicitudes QR y aplicaciones de cada cobro a una o más facturas. No exige que el titular se registre en el POS. Las bases existentes se migran conservando los cobros anteriores.
- El inicio de sesión usa una cookie de sesión local para asociar las operaciones al empleado que ingresó. Los PIN de demostración son públicos y no equivalen a seguridad de producción.
- `GET /api/lookup` consulta solo las facturas pendientes de la prestadora simulada. `POST /api/charge` confirma efectivo o tarjeta en ambas bases y registra las aplicaciones. `GET /api/history` filtra por el empleado de la sesión. `POST /api/qr/start` crea el QR y la página móvil confirma el pago simulado. Son endpoints **de este simulador**, no APIs oficiales.
- El backend valida la prestadora, la referencia, la pertenencia y el estado pendiente de cada factura; hace el cobro en una transacción SQLite y usa `request_id` para no duplicar reintentos.
- Las dos bases están anexadas a una misma conexión SQLite y usan journal `DELETE` para que el cambio conjunto sea atómico ante una caída, según la [documentación de SQLite](https://sqlite.org/lang_attach.html).
- Solo admite pago completo de facturas ya emitidas y un medio por cobro. Tarjeta y QR **no autorizan una transacción bancaria**: el cajero o el celular declaran la confirmación dentro de la simulación.
- No incluye gestión real de usuarios, recuperación de credenciales, control de intentos, conciliación bancaria, integración real ni comprobante fiscal. La página móvil solo permite consultar y confirmar una solicitud con token temporal; el POS completo permanece en `127.0.0.1`.

## Pruebas

```powershell
python -m unittest -v test_server.py
```

Las pruebas usan bases temporales: no cambian tus datos de `data/`.
