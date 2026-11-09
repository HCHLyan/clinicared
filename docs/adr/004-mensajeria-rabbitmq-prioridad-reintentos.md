# ADR 004: Topología RabbitMQ con prioridad, reintentos exponenciales y DLQ

* **Estado:** Aceptado
* **Fecha:** 2026-11-04
* **Historia:** US 04 – Tarea 1

## Contexto
Se envían recordatorios (RN-03) y ofertas de lista de espera (RN-06) a ~400 000 pacientes. Los
envíos de pacientes de riesgo alto y las ofertas de cupo deben adelantarse a los de riesgo bajo.
El proveedor de mensajería falla de forma transitoria (timeouts, 429, 5xx). Ningún mensaje se puede
perder y ninguno se puede enviar dos veces.

## Decisión
* Exchange `clinicared.notifications` (topic, durable). Routing keys `reminder.<riesgo>.<tipo>` y
  `waitlist.offer.created`. Así se pueden agregar consumidores (p. ej. analítica en tiempo real)
  sin tocar a los productores.
* Colas de trabajo **clásicas durables con `x-max-priority=10`**: oferta de lista de espera = 10,
  riesgo HIGH = 8, MEDIUM = 5, LOW = 2.
* **Reintentos exponenciales con una cola de espera por escalón** (`<cola>.retry.<ms>`, TTL fijo
  de 2 s, 8 s, 32 s, 128 s y 512 s). Al vencer, el mensaje vuelve a la cola de trabajo por la
  *default exchange*. El consumidor publica la copia con confirmación del broker y solo después
  hace `ack` del original. Si esa publicación falla, hace `nack(requeue=True)`.
* Tras 6 entregas, o si el mensaje viene mal formado o no se reconoce, `nack(requeue=False)` lo
  envía a la DLX `clinicared.notifications.dlx`, que lo deja en `<cola>.dlq`.
* Publicador con *publisher confirms*, `delivery_mode=2`, `mandatory=True` y
  `message_id = idempotency_key`.
* `prefetch=20` por consumidor y ack manual.
* Vhosts separados: `clinicared` (pacientes) y `airflow` (broker Celery).

## Alternativas descartadas
* **Plugin `rabbitmq_delayed_message_exchange`:** no está soportado en Amazon MQ.
* **TTL por mensaje en una sola cola de espera:** produce bloqueo de cabeza de cola. Un mensaje de
  512 s bloquea a uno de 2 s que llegó detrás.
* **Quorum queues:** son más resilientes, pero en RabbitMQ 3.13 no soportan `x-max-priority`. La
  durabilidad necesaria se obtiene con colas clásicas en el clúster multi-AZ de Amazon MQ, junto
  con el *outbox* y la reconciliación periódica (`recover_stuck_messages`).

## Consecuencias
* (+) Prioridad real por riesgo. Los fallos transitorios no saturan al proveedor.
* (+) Los mensajes envenenados quedan aislados en la DLQ, con alarma y runbook (RB-01).
* (−) Los escalones de backoff no tienen *jitter*. Se mitiga con `prefetch` y con escalado
  horizontal limitado.
