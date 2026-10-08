# POS de cobranzas — MVP educativo

Simula una boca de cobranzas que consulta facturas de ANDE, ESSAP y Tigo Hogar. La interfaz conserva el estilo sobrio de tablas del prototipo aprobado. **No hay conexión con esas empresas ni movimiento de dinero real.**

## Ejecutar

Requiere Python 3.10 o posterior. No necesita instalar paquetes.

```powershell
cd C:\ruta\al\pos-cobranza
python server.py
```

Abrí `http://127.0.0.1:8765` en el mismo equipo. Para reiniciar la simulación, detené el servidor y borrá los dos archivos de `data/` (esto elimina todos los cobros locales).

## Recorrido para probar

1. Iniciá sesión con Lucía Benítez (PIN `1234`) o Diego Rojas (PIN `5678`). Son credenciales públicas **solo para esta demostración**.
2. Abrí **Datos de prueba** para ver las referencias ficticias disponibles, sus titulares y saldos actuales. Pulsá **Consultar** en una fila para abrir esa cuenta.
3. Abrí tu caja, elegí una o más facturas pendientes y confirmá el cobro en efectivo. El total es exactamente la suma de las facturas elegidas.
4. Consultá de nuevo: las facturas cobradas figuran como pagadas. Compará **Pagos informados por la prestadora** con **Historial POS**.
5. Cerrá la caja para obtener su cantidad de cobros y total; después podés cerrar sesión.

## Modelo y límites

- Separación sencilla tipo MVC: las tablas SQLite son el **modelo**, las funciones y rutas de `server.py` controlan las operaciones, e `index.html` es la **vista** con interacción en JavaScript. No se añade un framework para imponer carpetas vacías.
- `data/prestadoras_simuladas.db`: cuentas, facturas y pagos de las prestadoras **ficticias**. Una cuenta se identifica por `(prestadora, referencia)`. El mismo titular puede tener servicios distintos sin mezclar facturas.
- `data/pos.db`: empleados, hashes de sus PIN, cajas, cobros y aplicaciones de cada cobro a una o más facturas. No exige que el titular se registre en el POS. La base existente se amplía automáticamente con las columnas de PIN.
- El inicio de sesión usa una cookie de sesión local para asociar las operaciones al empleado que ingresó. Los PIN de demostración son públicos y no equivalen a seguridad de producción.
- `GET /api/lookup` consulta la prestadora simulada. `POST /api/charge` confirma en ambas bases y registra las aplicaciones. `GET /api/history` muestra solo cobros hechos en este POS. Son endpoints **de este simulador**, no APIs oficiales.
- El backend valida la prestadora, la referencia, la pertenencia y el estado pendiente de cada factura; hace el cobro en una transacción SQLite y usa `request_id` para no duplicar reintentos.
- Las dos bases están anexadas a una misma conexión SQLite y usan journal `DELETE` para que el cambio conjunto sea atómico ante una caída, según la [documentación de SQLite](https://sqlite.org/lang_attach.html).
- Solo admite pago completo de facturas ya emitidas y efectivo. Son reglas conservadoras **del simulador**, no afirmaciones sobre las políticas reales de ANDE, ESSAP o Tigo.
- No incluye gestión real de usuarios, recuperación de credenciales, control de intentos, conciliación bancaria, integración real, comprobante fiscal ni operación por red. Escucha únicamente en `127.0.0.1`; está pensado para aprendizaje y desarrollo local.

## Pruebas

```powershell
python -m unittest -v test_server.py
```

Las pruebas usan bases temporales: no cambian tus datos de `data/`.
