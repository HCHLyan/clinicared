# ADR 003: Estrategia de Extracción Incremental Basada en Watermark

* **Estado:** Aceptado
* **Fecha:** 2026-10-15

## Contexto
A medida que el volumen de citas médicas (`appointments`) y pacientes (`patients`) crezca en la base de datos operativa, realizar extracciones completas (*Full Loads*) hacia la capa analítica impactará negativamente el rendimiento del motor transaccional y aumentará los tiempos de procesamiento.

## Decisión
Implementar un patrón de **Extracción Incremental** basado en marcas de agua (*Watermark*) utilizando las columnas temporales de auditoría (`created_at` / `updated_at`):
* Filtrar registros mediante intervalos acotados (`WHERE updated_at >= :watermark_start AND updated_at < :watermark_end`).
* Ordenar de forma determinista (`ORDER BY updated_at, id`) para evitar omisiones en empates de marca temporal.
* Actualizar el estado del *watermark* únicamente tras confirmar que la carga del lote finalizó con éxito.

## Consecuencias
* **Positivas:**
  * Reducción drástica de carga de lectura sobre PostgreSQL al extraer únicamente registros nuevos o modificados.
  * Recuperación segura ante fallos: si el pipeline falla, el *watermark* no avanza y el lote se reprocesa de forma segura.
* **Negativas / Riesgos:**
  * Requiere garantizar en la base de datos que la columna `updated_at` se actualice de manera confiable en cada modificación.
  * No detecta eliminaciones físicas (`DELETE`), por lo que las cancelaciones o bajas deben manejarse mediante estados lógicos (ej. `status = 'CANCELLED'`).