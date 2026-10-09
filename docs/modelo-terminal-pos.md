# Modelo de datos del terminal POS simulado

El POS guarda operaciones del **comercio** en `terminal.db`. El **emisor ficticio** mantiene tarjetas y fondos de prueba en `emisor_simulado.db`. Comparten una transacción SQLite durante una autorización simulada, pero tienen responsabilidades distintas. No hay datos de ANDE, ESSAP o Tigo en este nuevo módulo.

```mermaid
erDiagram
    operator ||--o{ batch : abre
    operator ||--o{ operation : cobra
    operator ||--o{ operator_session : inicia
    terminal ||--o{ batch : contiene
    batch ||--o{ operation : registra
    operation ||--o{ pin_attempt : recibe
    operation ||--o| qr_request : genera
    operation ||--o| operation : anulada_por
    account ||--o{ card : respalda
    card ||--o{ installment_plan : permite
    card ||--o{ operation : autoriza

    operator { int id PK; string name; string pin_digest; int failed_logins; datetime locked_until }
    operator_session { string token_digest PK; int operator_id FK; datetime expires_at }
    terminal { string id PK; string merchant_name }
    batch { int id PK; string terminal_id FK; int operator_id FK; string merchant_name; string operator_name; datetime opened_at; datetime closed_at }
    operation { string id PK; string request_id UK; int batch_id FK; int operator_id FK; string operator_name; string kind; string status; int amount; string card_id; string original_id FK }
    pin_attempt { string operation_id FK; string attempt_id PK; string outcome }
    qr_request { string token PK; string operation_id FK; datetime expires_at }
    account { string id PK; string kind; int available }
    card { string id PK; string account_id FK; string last4; int failed_pins; bool blocked }
    installment_plan { string card_id FK; int installments PK }
```

`card_id` en la operación es una referencia **lógica** a una tarjeta ficticia de la otra base; SQLite no impone una clave foránea entre archivos. No es un número de tarjeta real. El POS solo almacena `last4` inventados y la respuesta de autorización. El PIN de la tarjeta nunca se guarda en `terminal.db`; el emisor guarda su hash y el contador de errores. El PIN del empleado tampoco se guarda en claro: `operator` conserva un hash con sal y `operator_session` solo conserva el hash del token de sesión. Una operación `VOID` referencia con `original_id` la venta aprobada que anuló. Un índice impide una segunda anulación de la misma venta.

El lote conserva el nombre del comercio al abrirse; cada operación conserva el nombre del empleado que la hizo. Así, una reimpresión del ticket no cambia si luego se modifica el catálogo. Un lote puede contener operaciones de varios empleados. El resumen del lote es de la terminal; «Mis cobros» se filtra por `operation.operator_id` tomado de la sesión.

## Invariantes verificables

1. Una venta con el mismo `request_id` conserva su resultado; cambiar el importe o la tarjeta con ese identificador devuelve conflicto.
2. Un PIN reenviado con el mismo `attempt_id` no suma otro error. Tres intentos distintos incorrectos bloquean esa tarjeta de prueba.
3. Débito reduce `account.available` si hay saldo; crédito reduce el límite disponible por el importe **total**, aunque haya cuotas.
4. Solo `APPROVED` de tipo `CARD` o `QR` suma ventas. `VOID` aprobado resta del neto y devuelve fondos ficticios de tarjeta una sola vez.
5. Un QR pendiente puede pasar a `APPROVED`, `CANCELLED` o `EXPIRED`. Una confirmación repetida no cambia su resultado.
6. Cerrar el lote cancela solicitudes pendientes. Una venta del lote cerrado no puede anularse allí.
7. Una venta de tarjeta pendiente puede cancelarse antes de la autorización. Repetir la cancelación conserva el resultado; enviar un PIN después no la aprueba ni descuenta saldo.
8. Sin sesión, la API del POS rechaza consultas y cobros. Un empleado no puede consultar, completar, cancelar ni anular la operación de otro. El token QR temporal del celular solo confirma esa solicitud ficticia.

Los archivos [test_terminal_pos.py](../test_terminal_pos.py) y [test_terminal_api.py](../test_terminal_api.py) comprueban estas reglas sobre bases temporales.
