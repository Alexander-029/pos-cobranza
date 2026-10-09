# Zonas de lectura del terminal simulado

El aparato dibujado en este proyecto es **genérico**. La fotografía de referencia del usuario muestra un POS Dinelco, pero no se identificó de forma verificable el fabricante y modelo de ese equipo ni se encontró su manual de uso específico. Por eso, las zonas interactivas se basan en manuales oficiales de terminales móviles comparables; no se presentan como instrucciones para operar un Dinelco real.

| Gesto en la simulación | Zona | Fundamento |
|---|---|---|
| Acercar una tarjeta débito o crédito | Lector marcado en la parte superior | La [guía oficial PAX A920 Pro](https://www.pax.us/support/documents/a920-pro-quick-setup-guide/) indica aproximar la tarjeta al área señalada en la parte superior del terminal. |
| Insertar una tarjeta débito o crédito | Ranura inferior del chip | La [guía oficial PAX A920 Pro](https://www.pax.us/support/documents/a920-pro-quick-setup-guide/) indica insertar completamente la tarjeta con los contactos metálicos hacia arriba y hacia el dispositivo. La [guía oficial Ingenico Move 5000](https://ingenico.com/sites/default/files/resource-document/2022-10/MOVE5000%20-%20user%20guide%20-%20OCT22.pdf) también indica mantenerla insertada durante la transacción. |

**El chip no se acerca al lector superior.** La zona superior representa NFC/sin contacto; la ranura inferior representa la lectura por contacto del chip. Una misma tarjeta puede admitir ambas formas de lectura.

En la interfaz se muestran DEB-001 y CRE-001, datos ficticios de la base del emisor simulado. Arrastrarlas solo selecciona la tarjeta y el método de lectura; el servidor decide si pide PIN, aprueba o rechaza. No hay lectura de una tarjeta física ni comunicación con redes bancarias.
