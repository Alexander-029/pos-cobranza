# Datos para probar el POS de cobranzas

Este archivo es para quien prueba el sistema. **No forma parte de la pantalla del cajero.** Describe el estado inicial que `server.py` carga en `data/prestadoras_simuladas.db` cuando esa base todavía no tiene cuentas. Si ya hiciste cobros, el estado actual de tu base puede ser diferente.

## Qué se tomó de la realidad

- **HECHO:** ANDE permite consultar la factura ingresando el NIS; su [sitio de servicios](https://www.ande.gov.py/servicios/?op=9) muestra el campo «NIS».
- **HECHO:** el [portal de consulta de ESSAP](https://portal.essap.com.py/public/sigirci/sg78/sg78.xhtml) ofrece búsqueda por ISSAN o cuenta corriente catastral. Esta simulación elige ISSAN.
- **HECHO:** [Tigo indica](https://ayuda.tigo.com.py/hc/centro-de-ayuda/articles/6733176056044911-como-realizar-el-pago-a-traves-de-bocas-de-cobranzas) que en bocas de cobranzas se informa la cédula o el RUC del titular.
- **SIMULADO:** nombres, referencias, facturas, importes, vencimientos y pagos de las tablas siguientes. Los identificadores `DEMO-...` son deliberadamente ficticios; **no tienen el formato real de NIS, ISSAN ni CI/RUC** y no sirven en los portales de las prestadoras. La aplicación no consulta sistemas oficiales ni dispone de un convenio o ambiente de pruebas de ellas.

## Cuentas iniciales

Elegí la prestadora en el POS y copiá la referencia correspondiente. El titular **no se registra en el POS**: pertenece a la base simulada de la prestadora. La cuenta se identifica por el par **(prestadora, referencia)**.

| Prestadora | Referencia de prueba | Titular ficticio | Resultado inicial |
|---|---|---|---|
| ANDE | `DEMO-ANDE-001` | María González | Dos facturas pendientes y una pagada |
| ANDE | `DEMO-ANDE-000` | Ana Duarte | Sin deuda; una factura pagada |
| ESSAP | `DEMO-ESSAP-001` | María González | Una factura pendiente |
| Tigo Hogar | `DEMO-TIGO-001` | Carlos Medina | Una factura pendiente |

María González aparece en ANDE y ESSAP para probar que dos servicios de una misma persona no mezclan sus facturas. También se puede consultar una referencia existente bajo la prestadora equivocada: debe dar «no existe».

## Facturas iniciales

Los importes están en guaraníes. «Pagada» significa que la base simulada ya tiene un pago previo; esos pagos **no son cobros de este POS**.

| Prestadora | Referencia | Factura | Período | Disponible desde | Vencimiento | Importe | Estado inicial |
|---|---|---|---|---|---|---:|---|
| ANDE | `DEMO-ANDE-001` | `A-2026-07` | 07/2026 | 2026-07-28 | 2026-08-15 | ₲ 117.000 | Pagada |
| ANDE | `DEMO-ANDE-001` | `A-2026-08` | 08/2026 | 2026-08-28 | 2026-09-15 | ₲ 119.000 | Pendiente |
| ANDE | `DEMO-ANDE-001` | `A-2026-09` | 09/2026 | 2026-09-28 | 2026-10-15 | ₲ 128.000 | Pendiente |
| ANDE | `DEMO-ANDE-000` | `A-000-09` | 09/2026 | 2026-09-28 | 2026-10-15 | ₲ 83.000 | Pagada |
| ESSAP | `DEMO-ESSAP-001` | `E-2026-09` | 09/2026 | 2026-09-28 | 2026-10-20 | ₲ 64.000 | Pendiente |
| Tigo Hogar | `DEMO-TIGO-001` | `T-2026-09` | 09/2026 | 2026-09-28 | 2026-10-18 | ₲ 165.000 | Pendiente |

La fecha «Disponible desde» representa la publicación de la factura **en este simulador**, no una regla comprobada para cada prestadora. Una factura `PENDIENTE` solo se consulta y cobra a partir de esa fecha, según el calendario local del equipo servidor. El vencimiento no bloquea el pago anticipado de una factura ya disponible. La factura de agosto de ANDE está vencida al 8 de octubre de 2026, pero **el simulador no agrega recargos**: cobra exactamente el importe cargado en la factura. Esto es una decisión del MVP, no una afirmación sobre cómo calcula recargos ANDE.

## Cargar una factura futura con SQL

En un editor de SQLite, abrí **`data/prestadoras_simuladas.db`**, no `data/pos.db`. Para probar con una cuenta ya existente, ejecutá este ejemplo una sola vez (el `id` debe ser único):

```sql
INSERT INTO factura
  (id, prestadora, referencia, periodo, vencimiento, importe, estado, disponible_desde)
VALUES
  ('A-2026-11-PRUEBA', 'ANDE', 'DEMO-ANDE-001', '11/2026', '2026-12-15', 99000, 'PENDIENTE', '2026-11-28');
```

Volvé a consultar `DEMO-ANDE-001` en el POS. Antes del **28/11/2026**, esa factura no debe aparecer; desde ese día sí. No cambies la fecha del equipo para probar el límite: `test_server.py` comprueba el día anterior y el mismo día con un reloj controlado, sin tocar tus bases. Una factura ya disponible puede tener vencimiento posterior. Las facturas anteriores de una base creada antes de esta regla se conservan y se consideran disponibles.

## Pagos anteriores cargados en la prestadora simulada

| Prestadora | Referencia | Pago | Origen | Fecha UTC | Factura | Importe |
|---|---|---|---|---|---|---:|
| ANDE | `DEMO-ANDE-001` | `EXT-A-001` | Otro canal | 2026-08-12 14:00 | `A-2026-07` | ₲ 117.000 |
| ANDE | `DEMO-ANDE-000` | `EXT-A-000` | Otro canal | 2026-09-17 14:00 | `A-000-09` | ₲ 83.000 |

Al cobrar una factura pendiente, la base simulada registra el nuevo pago y la marca pagada; el POS guarda además el empleado, la caja, el medio de pago, el cobro y sus aplicaciones a facturas. Los pagos de otros canales **no se muestran al cajero**: solo afectan el estado de la deuda. «Mis cobros» contiene únicamente los cobros del empleado autenticado.

## Casos concretos de prueba

1. Consultá ANDE `DEMO-ANDE-001`: aparecen dos pendientes por **₲ 247.000** en conjunto. La factura ya pagada no aparece en el POS del cajero. Podés elegir una o ambas pendientes.
2. Consultá ANDE `DEMO-ANDE-000`: la cuenta existe, pero no hay facturas para cobrar; no se revela el pago anterior.
3. Consultá ESSAP `DEMO-ESSAP-001`: aparece **₲ 64.000** pendiente, sin mezclarse con las facturas ANDE de María González.
4. Consultá ANDE con `DEMO-ESSAP-001` o una referencia inexistente: la búsqueda debe indicar que no existe en esa prestadora simulada.
5. Cobrá una factura con la caja abierta y volvé a consultar: debe aparecer pagada y el cobro debe figurar en el historial de este POS. Una segunda confirmación de esa misma factura no debe crear otro cobro.

La fecha y los saldos del archivo representan **solo la semilla inicial**. Las bases locales de `data/` conservan los cobros entre ejecuciones y están excluidas de Git. Las pruebas automatizadas usan bases temporales.
