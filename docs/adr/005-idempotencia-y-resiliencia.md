# ADR 005: Idempotencia de extremo a extremo ("exactly-once efectivo")

* **Estado:** Aceptado
* **Fecha:** 2026-11-04
* **Historia:** US 04 – Tarea 5

## Contexto
RabbitMQ entrega *at-least-once*. Los webhooks del proveedor se reintentan y llegan en desorden. Los
DAG se re-ejecutan y hacen backfills. Un paciente **nunca** debe recibir el mismo recordatorio dos
veces.

## Decisión: cinco capas de deduplicación
1. **Llave determinista:** `sha256(namespace|v1|cita|versión|tipo|destinatario)` (`src/domain/idempotency.py`).
   No usa reloj ni aleatoriedad, así que replanificar produce la misma llave. Reprogramar una cita
   incrementa `appointments.version`; los recordatorios viejos quedan obsoletos y el dispatcher los
   marca `SKIPPED`.
2. **Unicidad en BD:** `UNIQUE(messages.idempotency_key)` más `INSERT … ON CONFLICT`. Los upserts
   del DAG solo modifican filas `PLANNED`.
3. **Claim atómico:** `UPDATE … SET status='SENDING' WHERE status IN ('PLANNED','ENQUEUED') OR lease vencido`.
   Solo un worker gana, por bloqueo de fila en PostgreSQL. Un mensaje en estado terminal se confirma
   como `DUPLICATE` sin efectos.
4. **Idempotencia del proveedor (requisito contractual):** la cabecera `Idempotency-Key` debe
   deduplicar durante al menos 24 h. Esto cierra la ventana entre "el proveedor aceptó" y
   "registramos `SENT`" cuando un worker muere en ese intervalo: el lease vence y el reenvío lleva
   la misma llave.
5. **Webhooks:** `webhook_events(provider_event_id PK)` da inserción única. Los estados avanzan
   monótonamente (`SENT < DELIVERED < READ`), así que un `SENT` tardío no pisa un `DELIVERED`. Un
   webhook **nunca** dispara un envío. Si el mensaje aún no es conocido, se responde 409 con
   rollback para que el proveedor reintente.

Fuera de orden: el dispatcher difiere el mensaje si llega antes de `send_at` (menos 5 min), lo
descarta si la versión cambió o la cita ya no está activa, y vuelve a validar RN-04 en el momento
del envío.

Transacciones de negocio a mensajería: patrón *transactional outbox* (`outbox_events`, relay con
`FOR UPDATE SKIP LOCKED`).

API: cabecera `Idempotency-Key` en `POST /appointments`. Guarda la respuesta en la misma
transacción, y si la llave se reutiliza con otro payload responde 422.

## Consecuencias
* (+) Las pruebas CA-08 cubren reentregas, 50 consumidores concurrentes, caída tras aceptación del
  proveedor, orden invertido y reintentos de webhook.
* (−) Depende de que el proveedor honre `Idempotency-Key`. Si no lo hace, la ventana residual
  (muerte del worker entre `send` y `mark_sent`, en segundos) puede duplicar. Esto se debe
  verificar en la selección del proveedor (riesgo R-03 del runbook).
