# Binance Pay y modo gratuito

En **Admin → Planes y pagos** puedes mostrar u ocultar los planes. Inicialmente están ocultos: los usuarios tienen generación gratuita de informes y glosarios, Word y PDF, sin consumir recargas ni cupos mensuales. Los documentos nuevos usan el plazo general de conservación; los anteriores conservan su vencimiento. Los límites normales regresan al mostrar los planes.

Los admins conservan acceso a `/plans` para probar compras reales, incluso en modo gratuito. Su acceso a la generación no consume cupo. Comprar un plan como admin requiere una verificación correcta, igual que para los demás usuarios.

## Configuración del servidor

El cliente «Olivier» ya se registró en el servicio de verificación y sus credenciales Binance de solo lectura fueron aceptadas. El token que devolvió el servicio quedó guardado en el `.env` local, excluido de Git.

Configura estas variables también en el alojamiento de producción; sincronizar el repositorio no copia el `.env`:

```dotenv
BINANCE_VERIFY_URL=https://binance-bmgnb2e5facwaqf5.canadacentral-01.azurewebsites.net
BINANCE_VERIFY_TOKEN=token_del_cliente
```

No se necesitan la clave maestra ni el API secret de Binance en EduLab. El ID público del destinatario se configura en Admin → Datos de cobro, como antes. No confundas ese ID con el token privado ni con el ID de la transacción que introduce el comprador.

## Verificación y reintentos

El pedido guarda el precio del catálogo y una referencia única antes de consultar la API. Envía el importe como texto con dos decimales, USDT y una antigüedad máxima de 24 horas. Los precios son los del catálogo de EduLab: no se copian los importes del script de ejemplo.

Un resultado `VERIFIED` con `verified: true` activa el plan automáticamente. La tolerancia de 0,01 USDT y los pagos por encima del precio los decide el servicio; EduLab no los invalida con una segunda comparación de importes.

Los estados con `retryable: true`, incluido `NOT_FOUND` cuando el servicio lo marca así, conservan el pedido. La página realiza hasta cinco comprobaciones automáticas, separadas por al menos seis segundos, y permite reintentar manualmente. El servidor evita llamadas concurrentes para el mismo pedido. Cada reintento conserva `orderReference`, por lo que una respuesta perdida después de reclamar el pago puede recuperarse sin duplicar la activación.

Un rechazo definitivo por importe, pago ya utilizado o pago no encontrado deja la compra rechazada. Si `NOT_FOUND` sigue siendo temporal, el comprador puede retirar esa referencia y corregir el ID. Esa corrección no se permite durante una verificación ni después de un fallo de red que haya dejado incierto si el pago fue reclamado.

El admin ve el estado de la API en la tabla de pagos. **Verificar Binance** consulta la API; no existe aprobación manual de Binance que omita esa validación. Pago Móvil mantiene su revisión manual.

## Validación realizada

Se probó la API real con un ID ficticio: respondió HTTP 200, `NOT_FOUND`, `verified: false`, `retryable: true`. Las pruebas automatizadas simulan confirmaciones, rechazos, reintentos, errores de red, concurrencia y fallos de persistencia. No se reclamó ningún pago real: falta probar una transacción real para confirmar el circuito completo de cobro.
