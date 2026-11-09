# Criterios de aceptación CA-01..CA-10 y su trazabilidad a pruebas

> ⚠️ El texto original de los CA no estaba en el repositorio. Esta matriz los **define** a partir de
> RN-01..RN-11 y debe ser aprobada por el Product Owner.

| CA | Criterio | Reglas | Pruebas |
|---|---|---|---|
| CA-01 | Una cita sin admisión hasta +20 min es inasistencia, salvo cancelación con ≥ 4 h. Los límites son exactos. | RN-01 | `tests/unit/test_ca01_no_show.py` |
| CA-02 | El riesgo se clasifica HIGH/MEDIUM/LOW según inasistencias en 6 meses y *lead time*, con razones explicables. | RN-02 | `test_ca02_risk.py` |
| CA-03 | HIGH recibe 72 h + 24 h, MEDIUM 24 h, LOW nada salvo primera cita. Las llaves son deterministas y versionadas. | RN-03 | `test_ca03_reminder_schedule.py` |
| CA-04 | Ningún envío entre 20:00 y 07:00 ni en festivo. Se corre al siguiente horario válido (o al anterior si es necesario). | RN-04, F5 | `test_ca04_quiet_hours.py`, `test_ca08_idempotency.py::test_quiet_hours_rechecked_at_send_time` |
| CA-05 | +1 cupo por cada 3 HIGH en el bloque, máximo +2, nunca en especialidades F7 no sobrecupables. | RN-05, F7 | `test_ca05_overbooking.py` |
| CA-06 | Al cancelar, el cupo se oferta a la lista de espera en ≤ 10 min, de forma determinista y excluyendo restringidos. | RN-06 | `test_ca06_waitlist.py`, `tests/integration/test_postgres.py::test_cancel_releases_slot_and_is_idempotent` |
| CA-07 | Un paciente con ≥ 3 inasistencias en 6 meses no puede autoagendar. La excepción del personal se justifica y audita. | RN-07 | `test_ca07_booking_restriction.py` |
| CA-08 | Reentregas, consumidores concurrentes, caídas, desorden y reintentos de webhook nunca duplican un mensaje. | Idempotencia | `test_ca08_idempotency.py`, `test_messaging_consumer.py`, `test_postgres.py::test_only_one_concurrent_claim_wins` |
| CA-09 | Los mensajes de un menor van solo al acudiente registrado; sin acudiente, no se envía. | RN-11 | `test_ca09_minors_guardian.py` |
| CA-10 | La capa analítica está seudonimizada y sin diagnósticos. Todo acceso a datos de paciente queda auditado de forma inmutable y *fail-closed*. | RN-09, RN-10 | `test_ca10_privacy_audit.py`, `test_spark_parity.py`, `test_postgres.py::test_audit_log_is_immutable_and_chained` |

Cobertura adicional: RN-08 (`test_rn08_occupancy.py`) y la simulación histórica
(`test_historical_simulation.py`).

```bash
pytest tests/unit                                          # sin infraestructura
INTEGRATION_DATABASE_URL=postgresql+psycopg2://... pytest -m integration tests/integration
pytest tests/dags                                          # requiere Airflow instalado
```
