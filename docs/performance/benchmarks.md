# Rendimiento (US 05 – Tarea 5): metodología y resultados

> **Estado: pendiente de ejecución.** Las tablas se llenan con los archivos que generan los scripts.
> No se registran cifras estimadas ni inventadas.

## 1. API: objetivo p95 ≤ 400 ms

**Instrumentación permanente:**
* Métrica EMF `ClinicaRed/ApiLatency` por ruta, emitida por `RequestContextMiddleware`.
* `TargetResponseTime` p95 del ALB, con la alarma `clinicared-api-p95-latency`.

**Prueba de carga:**
```bash
locust -f perf/locustfile.py --host https://<staging-alb> --headless -u 100 -r 10 -t 5m \
       --csv docs/performance/results/api_<fecha>
```
Debe correr en staging, con datos sintéticos (seeder) y la misma configuración de tareas ECS que
producción (512 CPU / 1024 MiB, 2 workers uvicorn).

| Fecha | Commit | Usuarios | Endpoint | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Errores | ¿Cumple? |
|---|---|---|---|---|---|---|---|---|---|
| _pendiente_ | | | `GET /patients/{id}` | | | | | | |
| _pendiente_ | | | `GET /patients` | | | | | | |
| _pendiente_ | | | `GET /health/ready` | | | | | | |

**Palancas aplicadas que afectan la latencia** (para comparar antes y después):
* Pool de conexiones con `pool_pre_ping` y `pool_recycle`, que evita reconexiones en caliente.
* Índices nuevos: `ix_appointments_patient_sched`, `ix_appointments_sched_status`,
  `ix_messages_due` (parcial) y la expresión de documento normalizado.
* Paginación con `ORDER BY id`, que es estable y usa la PK.
* La auditoría (RN-10) añade un INSERT por solicitud. Su costo se mide aparte comparando
  `/health/ready` (sin auditoría) con `/patients/{id}`.

## 2. Spark: tiempo de ejecución antes y después

```bash
PYTHONPATH=spark/jobs spark-submit --driver-memory 8g spark/benchmarks/run_benchmark.py \
    --lake-uri /tmp/clinicared-bench --patients 400000 --appointments 2500000 --runs 3 \
    --output docs/performance/results/spark_<fecha>.json
```
Las dos configuraciones se ejecutan sobre **la misma entrada**. Se reporta la mediana de 3
corridas, con la caché limpia entre corridas.

| Fecha | Entorno | Citas | Baseline (s, mediana) | Optimizado (s, mediana) | Speed-up |
|---|---|---|---|---|---|
| _pendiente_ | | 2 500 000 | | | |

| Optimización | Mecanismo | Verificación en Spark UI |
|---|---|---|
| Broadcast de dimensiones | `F.broadcast` + `autoBroadcastJoinThreshold=32MB` | `BroadcastHashJoin` en lugar de `SortMergeJoin` |
| Caché selectiva | `persist(MEMORY_AND_DISK)` de `fact_appointment` y `dim_patient` | Pestaña *Storage*; menos etapas repetidas |
| Particionado y columnar | Parquet snappy `partitionBy(year_month)` vs. CSV | Tamaño en disco; poda de particiones en Athena |
| AQE | `adaptive.enabled`, `coalescePartitions`, `skewJoin` | Menos tareas por etapa |
| Paralelismo JDBC | `partitionColumn=id`, `numPartitions=8` | 8 tareas en la etapa de lectura |
