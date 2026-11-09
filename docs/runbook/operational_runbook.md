# Runbook operativo: ClínicaRed Citas

**Audiencia:** guardia de plataforma (DevOps y Data). **Canal de alertas:** SNS `clinicared-alerts`
(correo de guardia). **Dashboard:** CloudWatch `clinicared-operations`.

## 0. Mapa del sistema

| Componente | Dónde | Cómo se ve sano |
|---|---|---|
| API FastAPI | ECS `clinicared-api` tras el ALB | `/health/ready` 200; p95 ≤ 400 ms |
| Workers de recordatorios y lista de espera | ECS `clinicared-reminder-worker` / `-waitlist-worker` | `QueueDepth` baja; `DlqDepth = 0` |
| Outbox relay | ECS `clinicared-outbox-relay` | `OutboxPublishFailures = 0` |
| RabbitMQ | Amazon MQ `clinicared-rabbitmq` (vhosts `clinicared`, `airflow`) | Consola: consumidores > 0 |
| Airflow | ECS (scheduler, webserver y workers Celery) | DAGs en verde; SLA de las 04:00 |
| Spark | EMR Serverless `clinicared-spark` | Job `build_gold` exitoso a diario |
| PostgreSQL | RDS (US 03) | Conexiones < 80 %, sin bloqueos largos |

### Arranque inicial (una sola vez por entorno)
1. `psql -f docs/database/01_init_schema.sql`, luego `02_us04_schema.sql`. Ambos son idempotentes.
2. Asignar contraseñas a los roles `clinicared_app`, `clinicared_etl` y `clinicared_auditor` (con
   `LOGIN`), y guardarlas en `clinicared/db` y `clinicared/etl-db`.
3. Crear los vhosts en Amazon MQ (API de administración, desde una tarea dentro de la VPC):
   ```bash
   curl -u "$MQ_USER:$MQ_PASS" -X PUT https://<broker>.mq.<region>.amazonaws.com/api/vhosts/clinicared
   curl -u "$MQ_USER:$MQ_PASS" -X PUT https://<broker>.mq.<region>.amazonaws.com/api/vhosts/airflow
   ```
   Las colas, exchanges y DLQ los declara cada proceso al iniciar (declaración idempotente).
4. `python -m src.seeders.seed_reference_data --years 2026 2027 --f5 <F5.csv> --f7 <F7.csv>`
5. Cargar el token del proveedor:
   `aws secretsmanager put-secret-value --secret-id clinicared/messaging-gateway-token --secret-string ...`

---

## RB-01 · Alarma `clinicared-reminders-dlq-not-empty` / `clinicared-waitlist-dlq-not-empty` (falla demostrada)

**Significado:** hay mensajes en `q.reminders.dispatch.dlq` o `q.waitlist.offers.dlq`. Pueden ser
JSON inválido, una llave desconocida (rechazo) o 6 entregas fallidas. También se dispara si la
métrica deja de llegar (`treat_missing_data = breaching`), porque eso indica que el worker o su
sonda están caídos.

**Impacto:** pacientes sin recordatorio u ofertas de cupo no enviadas (RN-06).

**Diagnóstico (≤ 10 min):**
1. Dashboard: ¿subió `DispatchOutcome{Outcome=RETRY}` antes? Si sí, el proveedor está degradado.
   Si `DlqDepth` no tiene datos, el worker está caído (ver ECS → eventos del servicio).
2. Inspeccionar sin consumir:
   `python scripts/ops/dlq_tool.py inspect --queue reminders --limit 20`
   El campo `reason` indica `rejected` (llave desconocida o mal formada) o `expired`.
3. Logs Insights (`/clinicared/workers`):
   ```
   fields @timestamp, message, idempotency_key, error
   | filter message in ["message dead-lettered", "transient provider error", "dispatch for unknown key"]
   | sort @timestamp desc | limit 50
   ```

**Recuperación:**

| Causa | Acción |
|---|---|
| Proveedor caído (RETRY y luego DLQ) | Confirmar que el proveedor se recuperó y ejecutar `dlq_tool.py replay --queue reminders`. Es seguro: el dispatcher descarta duplicados y obsoletos. |
| Llaves desconocidas (`chaos:` o datos borrados) | `dlq_tool.py discard --queue reminders --only-chaos`. Si no son de caos, abrir un incidente de datos. |
| JSON inválido | Bug de productor: hacer rollback del último despliegue (ECS → revisión anterior) y luego descartar. |
| Worker caído | ECS → *Force new deployment*. Revisar memoria y CPU. Los mensajes esperan en la cola. |

**Verificación:** `DlqDepth = 0` y la alarma en OK. Correr `OC-02` y `OC-07` de
`sql/analytics/03_operational_controls_postgres.sql`: no debe haber duplicados ni mensajes
atascados. Si había mensajes en ENQUEUED, el DAG de orquestación los re-encola en menos de 1 h,
o se puede disparar a mano.

### Procedimiento de demostración (staging)
1. Confirmar que la alarma está en OK y que el dashboard muestra `DlqDepth = 0`.
2. `RABBITMQ_URL=... python scripts/chaos/trigger_dlq_alarm.py --count 3 --i-understand-this-is-not-production`
3. Esperar de 2 a 4 minutos (sonda cada 60 s, alarma con 2 de 3 datapoints). Llega el correo SNS
   "ALARM: clinicared-reminders-dlq-not-empty".
4. Diagnosticar con `dlq_tool.py inspect`: las 3 llaves `chaos:*` aparecen con `reason=rejected`.
   Ningún paciente fue contactado (no existen filas en `messages`).
5. Recuperar con `dlq_tool.py discard --queue reminders --only-chaos`.
6. Esperar el correo "OK". Registrar en la bitácora de incidentes: hora de inyección, hora de la
   alarma, MTTA y MTTR, y capturas del dashboard.

---

## RB-02 · Latencia API p95 > 400 ms o 5xx

1. Dashboard: ¿coincide con un despliegue? Si sí, hacer rollback (ECS → revisión anterior; el
   *circuit breaker* ya revierte las fallas de arranque).
2. ¿Hay saturación de BD? Revisar RDS Performance Insights: consultas top y esperas `Lock`.
3. ¿La auditoría está lenta? `ApiLatency` de `/patients/{id}` frente a `/health/ready`. Si el
   INSERT de `audit_log` espera el *advisory lock* de la cadena, revisar la tasa de accesos y
   escalar con el ADR 009 (particionar la cadena por día).
4. Escalar horizontalmente: `aws ecs update-service --desired-count +2`.
5. Un 503 con "Audit unavailable" significa que el modo fail-closed de RN-10 está activo: la BD no
   acepta inserts en `audit_log`. Restaurar la BD; **no** desactivar la auditoría.

## RB-03 · Outbox o broker (alarmas `outbox-publish-failures`, `mq-memory`)
1. Amazon MQ: estado del broker, conexiones y memoria. Por encima del *high watermark*, RabbitMQ
   bloquea a los publicadores.
2. ¿Se rotaron credenciales? Reiniciar los servicios ECS para que relean los secretos.
3. Los eventos no se pierden: quedan en `outbox_events` con `published_at IS NULL` y el relay los
   publica al volver.
4. Medir el atraso: `SELECT count(*), min(created_at) FROM outbox_events WHERE published_at IS NULL;`

## RB-04 · RN-06: SLA de liberación de cupos (> 10 min)
La liberación es síncrona, así que una latencia alta implica una transacción de cancelación lenta
o bloqueada.
1. `OC-01` (p95 y máximo). Buscar bloqueos sobre `waitlist` y `waitlist_offers` en
   `pg_stat_activity`.
2. Verificar el DAG `clinicared_reminder_orchestration` (tarea `waitlist_sweeper`) para
   re-ofertas.

## RB-05 · Muchas fallas permanentes del proveedor
1. Agrupar `last_error` en `messages` con status FAILED de la última hora.
2. Si son "invalid number", es un problema de calidad de datos F1 o OLTP: reportarlo al dueño de
   F1. Si es autenticación, rotar el token del proveedor.

## RB-06 · DAG fallido (correo de `on_failure_callback`)

| DAG | Efecto de no correr | Reejecución |
|---|---|---|
| admissions_consolidation | RN-01 desactualizado; el riesgo usa datos del día anterior | *Clear* de la tarea; es idempotente |
| risk_calculation | Sin recordatorios nuevos | *Clear*. Si F6 falta, la tarea sigue sin clima. |
| reminder_orchestration | Envíos retrasados hasta 10 min por corrida perdida | Automático en la siguiente corrida |
| analytics_spark_etl | Power BI desactualizado (no afecta pacientes) | *Clear*; `dynamic overwrite` lo hace idempotente |

---

## Riesgos conocidos

| Id | Riesgo | Mitigación |
|---|---|---|
| R-01 | Pérdida del *pepper* RN-09 | Secreto con `recovery_window` de 30 días y respaldo cifrado fuera de línea |
| R-02 | Airflow caído en la ventana 02:00–04:00 | Sensor `soft_fail`; los recordatorios ya planificados siguen saliendo por la orquestación cada 10 min |
| R-03 | El proveedor no honra `Idempotency-Key` | Prueba contractual antes de producción (ADR 005) |
| R-04 | Contraseña de BD histórica en el historial Git (antiguo `config.py` / `docker-compose.yml`) | Rotar en todos los entornos y purgar el historial (`git filter-repo`); gitleaks la detectará mientras exista |
