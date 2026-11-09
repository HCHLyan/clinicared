# ADR 007: Orquestación con Apache Airflow (CeleryExecutor + RabbitMQ)

* **Estado:** Aceptado
* **Fecha:** 2026-11-04
* **Historia:** US 04 – Tarea 2

## Decisión
| DAG | Horario (Bogotá) | Responsabilidad |
|---|---|---|
| `clinicared_admissions_consolidation` | 02:00 | Carga F3 (upsert por `admission_id`) y evalúa RN-01 sobre el día y los 3 previos (archivos tardíos). Publica el dataset `attendances`. |
| `clinicared_risk_calculation` | 03:30 | Carga F5/F6, calcula RN-02 para los próximos 7 días, planifica RN-03/04/11 y calcula la capacidad RN-05. El sensor sobre admisiones es `soft_fail`: si F3 se atrasa, el riesgo corre con los datos del día anterior en lugar de dejar pacientes sin recordatorio. |
| `clinicared_reminder_orchestration` | cada 10 min | Recupera mensajes atascados, publica los vencidos con prioridad y expira/re-oferta la lista de espera. |
| `clinicared_analytics_spark_etl` | 04:00 | Spark: Bronze → Silver → Gold. |

Principios:
* **DAG delgados:** la lógica vive en `src/services` y `src/domain`, que tienen pruebas unitarias.
  Los imports pesados van dentro de las tareas para que el parseo sea rápido.
* **Idempotencia:** cada tarea escribe con upserts por llave de negocio, o con
  `partitionOverwriteMode=dynamic` en el caso de Spark.
* `catchup=False`, `max_active_runs=1`, reintentos exponenciales y `on_failure_callback` con SNS.
* La fecha lógica (`data_interval_*`) se usa para cálculos de negocio; la hora real
  (`local_now`) solo para decisiones de envío.
* CeleryExecutor con broker RabbitMQ en el vhost `airflow`, separado del tráfico de pacientes.

## Alternativas
* **MWAA:** está gestionado, pero su CeleryExecutor usa SQS y no RabbitMQ, lo que no cumple el
  requisito. Además, su costo mínimo por entorno es alto para cuatro DAG. Se recomienda
  reevaluarlo si el número de DAG crece.
* **Cron + scripts:** no da reintentos, linaje, sensores ni visibilidad.

## Consecuencias
* (−) Operar Airflow en ECS (webserver, scheduler y workers) es responsabilidad del equipo. Las
  definiciones de tareas ECS de Airflow todavía no están en este repositorio (pendiente explícito,
  ver README).
