# ADR 006: Interpretación de reglas de negocio y supuestos explícitos

* **Estado:** Propuesto. **Requiere validación del Product Owner.**
* **Fecha:** 2026-11-04

Las reglas RN-01..RN-11 dejan detalles abiertos. Cada supuesto está aislado en una constante del
dominio (`src/domain/rules/*.py`), así que un cambio de criterio toca una sola línea y su prueba.

| Regla | Supuesto adoptado | Constante |
|---|---|---|
| RN-01 | Una admisión **posterior** a +20 min cuenta como inasistencia (`ADMITTED_AFTER_GRACE`). Una cancelación con menos de 4 h es inasistencia. Ambos límites son inclusivos (=20 min asiste; =4 h a tiempo). | `NO_SHOW_GRACE`, `CANCELLATION_NOTICE` |
| RN-01/02/07 | "6 meses" = 6 meses calendario hacia atrás, en ventana semiabierta `[as_of−6m, as_of)`. | `HISTORY_WINDOW_MONTHS` |
| RN-02 | *Lead time* = días calendario entre la fecha de reserva y la fecha de la cita. "Confirmada" = `confirmed_at` no nulo. | `risk.py` |
| RN-03 | Primera cita = sin citas previas no canceladas. Un recordatorio vencido se omite si queda uno posterior; el último se envía como *catch-up*. Ningún recordatorio se entrega a menos de 2 h de la cita. | `MIN_LEAD_BEFORE_APPOINTMENT` |
| RN-04 | Ventana válida `[07:00, 20:00)` en hora de Bogotá. Festivos = F5 ∪ calendario legal (Ley Emiliani). Si correr el envío hacia adelante lo deja demasiado tarde, se usa hacia atrás 19:30 del día hábil previo. | `QUIET_START/END`, `BACKWARD_ANCHOR` |
| RN-05 | "Slot" = bloque de agenda del profesional en una sede, de 60 min (configurable). Solo cuentan citas HIGH regulares no canceladas. Solo aplica dentro del horizonte de 7 días con riesgo calculado. | `OVERBOOK_BLOCK_MINUTES` |
| RN-06 | La liberación (crear la oferta) es síncrona en la transacción de cancelación, así que la latencia es de milisegundos. La **notificación** sí respeta RN-04. Si el candidato no responde en 30 min, la oferta pasa al siguiente. No se ofertan cupos que empiecen en menos de 1 h. Cancelar un sobrecupo no libera cupo. | `OFFER_TTL`, `MIN_OFFER_LEAD` |
| RN-07 | "Restringido" = no puede reservar por autoservicio, call center ni lista de espera. El personal puede reservar con justificación (≥10 caracteres), que queda auditada. | `RESTRICTION_THRESHOLD` |
| RN-08 | Ocupación efectiva = atendidos (regulares + sobrecupo) / capacidad regular ofertada. Puede superar 1,0. | `occupancy.py` |
| RN-09 | El seudónimo es SHA-256 con *pepper* secreto (ver ADR 009). Los diagnósticos se reducen a `has_chronic_condition` desde Silver. | `privacy.py` |
| RN-11 | Menor = menos de 18 años en la fecha de la cita. Si la edad es desconocida, se escribe al acudiente si existe; si no existe, no se envía nada. | `ADULT_AGE` |
| F6 | El clima **no** altera RN-02 (la regla es cerrada). Se usa para un aviso en el texto y para analítica. Severo = precipitación ≥ 30 mm o tormenta. | `SEVERE_PRECIPITATION_MM` |

Los contratos de F1–F7 están en [`docs/data_sources.md`](../data_sources.md) y los criterios de
aceptación CA-01..CA-10 en [`docs/testing/acceptance_criteria.md`](../testing/acceptance_criteria.md).
Ambos documentos se redactaron por inferencia porque el repositorio no los incluía.
