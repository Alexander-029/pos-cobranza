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

1. Abrí la caja con un empleado.
2. Seleccioná ANDE y consultá `DEMO-ANDE-001`. Tiene dos facturas pendientes y una pagada por otro canal.
3. Seleccioná una o ambas facturas y confirmá el cobro en efectivo. El total es exactamente la suma de las facturas elegidas.
4. Consultá de nuevo: las facturas cobradas figuran como pagadas. Compará **Pagos informados por la prestadora** con **Historial POS**.
5. Consultá `DEMO-ANDE-000` para ver una cuenta sin deuda, `DEMO-ESSAP-001` y `DEMO-TIGO-001` para otras prestadoras, o una referencia inexistente.
6. Cerrá la caja para obtener su cantidad de cobros y total.

## Modelo y límites

- Separación sencilla tipo MVC: las tablas SQLite son el **modelo**, las funciones y rutas de `server.py` controlan las operaciones, e `index.html` es la **vista** con interacción en JavaScript. No se añade un framework para imponer carpetas vacías.
- `data/prestadoras_simuladas.db`: cuentas, facturas y pagos de las prestadoras **ficticias**. Una cuenta se identifica por `(prestadora, referencia)`. El mismo titular puede tener servicios distintos sin mezclar facturas.
- `data/pos.db`: empleados, cajas, cobros y aplicaciones de cada cobro a una o más facturas. No exige que el titular se registre en el POS.
- `GET /api/lookup` consulta la prestadora simulada. `POST /api/charge` confirma en ambas bases y registra las aplicaciones. `GET /api/history` muestra solo cobros hechos en este POS. Son endpoints **de este simulador**, no APIs oficiales.
- El backend valida la prestadora, la referencia, la pertenencia y el estado pendiente de cada factura; hace el cobro en una transacción SQLite y usa `request_id` para no duplicar reintentos.
- Las dos bases están anexadas a una misma conexión SQLite y usan journal `DELETE` para que el cambio conjunto sea atómico ante una caída, según la [documentación de SQLite](https://sqlite.org/lang_attach.html).
- Solo admite pago completo de facturas ya emitidas y efectivo. Son reglas conservadoras **del simulador**, no afirmaciones sobre las políticas reales de ANDE, ESSAP o Tigo.
- No incluye autenticación, autorización, conciliación bancaria, integración real, comprobante fiscal ni operación por red. Escucha únicamente en `127.0.0.1`; está pensado para aprendizaje y desarrollo local.

## Pruebas

```powershell
python -m unittest -v test_server.py
```

Las pruebas usan bases temporales: no cambian tus datos de `data/`.
