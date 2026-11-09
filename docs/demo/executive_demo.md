# Escenario de demostración ejecutiva (≈ 20 min)

**Audiencia:** dirección médica, operaciones y finanzas. **Entorno:** staging con datos sintéticos
(nunca datos reales).

## Preparación (el día anterior)
1. `docker compose up -d` (o staging AWS) y luego `python src/seeders/generate_seed.py` y
   `python -m src.seeders.seed_reference_data --years 2026`.
2. Correr los DAG `risk_calculation` y `analytics_spark_etl`, y refrescar Power BI.
3. Ejecutar `python -m src.analytics.historical_simulation --from-db --out demo_sim.json`.

## Guion

| Min | Mensaje para negocio | Qué se muestra |
|---|---|---|
| 0–3 | "Hoy no sabemos quién va a faltar; enviamos lo mismo a todos." | Power BI: tasa de inasistencia por sede y especialidad (PN-01) y ocupación efectiva (PN-03) |
| 3–7 | "Cada noche clasificamos el riesgo de las citas de la semana con reglas claras." | Airflow: DAG de riesgo en verde. SQL: distribución HIGH/MEDIUM/LOW con sus razones (`risk_scores.reasons`) |
| 7–10 | "Solo escribimos a quien lo necesita, nunca de noche ni en festivo, y a los niños vía su acudiente." | Tabla `messages`: horarios rodados de 21:00 a 07:00; un menor con `recipient_type = GUARDIAN` |
| 10–13 | "Si alguien cancela, en segundos ofrecemos el cupo a la lista de espera." | `POST /appointments/{id}/cancel` en vivo: respuesta con `offer_id`; el simulador recibe el SMS de oferta |
| 13–16 | "¿Cuánto ahorramos?" | `demo_sim.json`: costo y tasa de inasistencia en "masivo" frente a "por riesgo", y la tabla de sensibilidad |
| 16–18 | "¿Y si algo falla?" | Inyección de falla RB-01: llega el correo de alarma y se recupera con `dlq_tool.py` |
| 18–20 | "Los datos de los pacientes están protegidos." | Gold sin nombres ni documentos (QC-03); `audit_log_verify_chain()` vacío |

## Mensajes clave
* Las reglas son explicables (cada riesgo tiene una razón) y auditables.
* Las cifras de impacto de la simulación dependen del supuesto de efectividad del recordatorio
  (r1, r2). **La recomendación es un piloto A/B de 8 semanas en 2 sedes** para calibrarlo antes de
  extenderlo a las 22.
