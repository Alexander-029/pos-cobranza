# Diagrama de procesos del terminal POS simulado

Este diagrama describe el **nuevo terminal de pagos propuesto**. Todavía no representa el funcionamiento de la aplicación de facturas que está en la raíz del repositorio. Los cobros, tarjetas, PIN y autorizaciones del diagrama son ficticios.

## Verlo en VS Code

Abrí este archivo `.md` y presioná **Ctrl+Shift+V** para abrir la vista previa de Markdown, o **Ctrl+K, V** para verla al lado del texto. VS Code renderiza los bloques `mermaid` en su vista previa integrada; no hace falta instalar una extensión. El diagrama se puede editar como texto y la vista previa reflejará los cambios. [Documentación oficial de VS Code](https://code.visualstudio.com/docs/languages/markdown#_mermaid-diagram-rendering).

## Venta

```mermaid
flowchart TD
    A["Cliente llega; operador ingresa el importe"] --> B{"¿Tarjeta o QR?"}

    B -->|Tarjeta| C["Acercar, insertar o deslizar tarjeta ficticia"]
    C --> D{"¿Tarjeta activa y lectura admitida?"}
    D -->|No| R["Registrar rechazo; no sumar al total"]
    D -->|Sí| E{"¿Crédito con cuotas habilitadas?"}
    E -->|Sí| F["Elegir plan disponible"]
    E -->|No| G["Determinar verificación según tarjeta, lectura e importe"]
    F --> G
    G -->|Pide insertar chip| C2["Solicitar inserción de la misma tarjeta"]
    C2 --> D
    G -->|Pide PIN| H["Ingresar PIN ficticio"]
    H --> I{"¿PIN correcto?"}
    I -->|No| J["Sumar intento en emisor simulado"]
    J --> K{"¿Tres errores?"}
    K -->|No| H
    K -->|Sí| L["Bloquear tarjeta de prueba"]
    L --> R
    I -->|Sí| M["Solicitar autorización al emisor simulado"]
    G -->|No pide PIN| M
    M --> N{"¿Saldo o límite disponible?"}
    N -->|No| R
    N -->|Sí| P["Aprobar y afectar fondos o límite una sola vez"]

    B -->|QR| Q["Generar QR temporal con el importe"]
    Q --> S["Cliente escanea; el celular muestra comercio e importe"]
    S --> T{"¿Confirma antes de vencer?"}
    T -->|No| U["Cancelar o vencer; total sin cambios"]
    T -->|Sí| V["Confirmar pago simulado una sola vez"]

    P --> W["Guardar venta aprobada y generar ticket"]
    V --> W
    W --> X["Mostrar ticket y sumar venta al lote"]
    R --> Y["Fin de esta venta"]
    U --> Y
    X --> Y
```

La verificación del titular (por ejemplo, PIN) y la autorización del importe son pasos distintos. Un PIN correcto puede ir seguido de un rechazo por falta de fondos. La regla de **tres errores** pertenece solo al emisor ficticio de esta simulación; no afirma una regla universal bancaria.

## Después de la venta

El operador puede consultar operaciones y reimprimir un ticket. Puede anular una venta aprobada mientras el lote esté abierto. Al terminar el turno, cierra el lote y consulta el total de **ventas aprobadas menos anulaciones**, desglosado por medio. El cierre no ocurre automáticamente después de cada cliente ni representa un depósito bancario.
