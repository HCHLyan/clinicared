# ADR 008: Lake en Parquet con Spark, esquemas explícitos y optimizaciones medidas

* **Estado:** Aceptado
* **Fecha:** 2026-11-04
* **Historias:** US 04 – Tarea 4, US 05 – Tarea 4

## Decisión
* **Zonas:** `landing/` (F1, F5, F6, F7 crudos), `silver/` (tipado y limpio), `gold/` (estrella) y
  `quarantine/`.
* **Esquemas explícitos** para toda fuente (`schemas.py`). Los CSV se leen como texto con
  `_corrupt_record` y se castean explícitamente. Así un valor inválido termina en cuarentena o en
  un `dq_flag`, y nunca se vuelve un NULL silencioso.
* **Limpieza F1:** edades negativas o mayores a 120 se anulan (`AGE_OUT_OF_RANGE`); fechas de
  nacimiento futuras o anteriores a 1900 también (`BIRTH_DATE_INVALID`). La edad se recalcula desde
  la fecha de nacimiento (`AGE_MISMATCH` si difiere más de 1 año). Se deduplica por documento
  normalizado conservando el último `updated_at`.
* **Formato:** Parquet + snappy, con hechos particionados por `year_month`. Se eligió Parquet sobre
  Delta porque no exige JARs adicionales, Athena y Power BI lo leen nativamente, y la idempotencia
  se logra re-extrayendo una ventana rodante de 400 días con *dynamic partition overwrite*. Si el
  volumen hace inviable la re-extracción, migrar a Delta o Iceberg y usar `MERGE` incremental
  sobre `updated_at` (ADR 003).
* **Optimizaciones** (modo `optimized` frente a `baseline` en `build_gold.py`):
  1. *Broadcast join* de dimensiones pequeñas (sedes, especialidades, clima, mapa de llaves de
     paciente). Elimina el *shuffle* del lado grande.
  2. *Cache* selectivo (`MEMORY_AND_DISK`) de `fact_appointment` y `dim_patient`, que se reutilizan
     en 3 o más salidas. No se cachea nada que se use una sola vez.
  3. `repartition(year_month)` antes de escribir particionado: menos archivos pequeños y poda de
     particiones en Athena.
  4. AQE (coalesce y skew join) y `shuffle.partitions=64` en lugar de 200.
  5. Lectura JDBC paralela por rangos de `id` (`numPartitions=8`).
* **Clave seudónima con `sha2` nativo** (sin UDF Python), lo que evita serializar 400 000 filas
  hacia procesos Python.

## Medición
`spark/benchmarks/run_benchmark.py` genera un Silver sintético a escala (400 000 pacientes,
2,5 M de citas) y ejecuta ambos modos N veces sobre la misma entrada. Los resultados van en
`docs/performance/benchmarks.md`. **Todavía no hay resultados registrados.**
