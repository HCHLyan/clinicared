-- =============================================================================
-- Consultas de control y preguntas de negocio (Athena / Trino SQL).
-- Cada bloque indica la pregunta (PN-xx) o el control (QC-xx) que responde.
-- Base de tasas de inasistencia: citas evaluadas (ATTENDED + NO_SHOW).
-- =============================================================================

-- PN-01 Tasa de inasistencia por sede y especialidad (últimos 6 meses)
SELECT b.name AS sede, s.name AS especialidad,
       count(*) AS citas_evaluadas,
       sum(f.is_no_show) AS inasistencias,
       round(100.0 * sum(f.is_no_show) / nullif(sum(f.is_no_show) + sum(f.is_attended), 0), 2) AS tasa_inasistencia_pct
FROM clinicared_gold.fact_appointment f
JOIN clinicared_gold.dim_branch b ON b.id = f.location_id
JOIN clinicared_gold.dim_specialty s ON s.id = f.specialty_id
WHERE f.year_month >= date_format(date_add('month', -6, current_date), '%Y-%m')
  AND f.outcome IN ('ATTENDED', 'NO_SHOW')
GROUP BY 1, 2
ORDER BY tasa_inasistencia_pct DESC;

-- PN-02 Inasistencia según clima (F6) y festivos (F5)
SELECT f.weather_condition, f.is_severe_weather, d.is_holiday,
       count(*) AS citas,
       round(100.0 * avg(f.is_no_show), 2) AS tasa_inasistencia_pct
FROM clinicared_gold.fact_appointment f
JOIN clinicared_gold.dim_date d ON d.date_key = f.date_key
WHERE f.outcome IN ('ATTENDED', 'NO_SHOW')
GROUP BY 1, 2, 3
ORDER BY tasa_inasistencia_pct DESC;

-- PN-03 Ocupación efectiva (RN-08) mensual por sede
SELECT a.year_month, b.name AS sede,
       sum(a.regular_capacity) AS capacidad_regular,
       sum(a.attended_regular + a.attended_overbook) AS atendidos,
       round(1.0 * sum(a.attended_regular + a.attended_overbook) / nullif(sum(a.regular_capacity), 0), 4) AS ocupacion_efectiva,
       round(1.0 * sum(a.booked_regular) / nullif(sum(a.regular_capacity), 0), 4) AS ocupacion_nominal,
       sum(a.lost_capacity) AS capacidad_perdida
FROM clinicared_gold.agg_occupancy_daily a
JOIN clinicared_gold.dim_branch b ON b.id = a.location_id
GROUP BY 1, 2
ORDER BY 1, 2;

-- PN-04 ROI de recordatorios: inasistencia con vs. sin recordatorio, por nivel de riesgo
SELECT f.risk_level,
       CASE WHEN f.reminders_sent > 0 THEN 'con_recordatorio' ELSE 'sin_recordatorio' END AS grupo,
       count(*) AS citas,
       round(100.0 * avg(f.is_no_show), 2) AS tasa_inasistencia_pct,
       sum(f.reminder_cost_cop) AS costo_mensajeria_cop
FROM clinicared_gold.fact_appointment f
WHERE f.outcome IN ('ATTENDED', 'NO_SHOW')
GROUP BY 1, 2
ORDER BY 1, 2;
-- Nota: es una comparación observacional (sesgo de selección: el riesgo decide quién recibe).
-- La estimación causal usa src/analytics/historical_simulation.py y un A/B controlado.

-- PN-05 Costos de mensajería por mes, canal, riesgo y destinatario (RN-11)
SELECT r.year_month, r.channel, r.risk_level, r.recipient_type,
       sum(r.is_sent) AS enviados, sum(r.is_delivered) AS entregados,
       round(100.0 * sum(r.is_delivered) / nullif(sum(r.is_sent), 0), 2) AS tasa_entrega_pct,
       sum(r.cost_cop) AS costo_cop,
       round(sum(r.cost_cop) / nullif(sum(r.is_delivered), 0), 2) AS costo_por_entregado_cop
FROM clinicared_gold.fact_reminder r
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4;

-- PN-06 Impacto del sobrecupo (RN-05): asistencia de sobrecupos y ocupación en días con/sin sobrecupo
WITH dias AS (
    SELECT location_id, specialty_id, date_key,
           sum(attended_overbook) AS atendidos_sobrecupo,
           max(effective_occupancy) AS ocupacion_efectiva
    FROM clinicared_gold.agg_occupancy_daily GROUP BY 1, 2, 3
)
SELECT CASE WHEN atendidos_sobrecupo > 0 THEN 'con_sobrecupo' ELSE 'sin_sobrecupo' END AS tipo_dia,
       count(*) AS dias, round(avg(ocupacion_efectiva), 4) AS ocupacion_efectiva_prom,
       sum(atendidos_sobrecupo) AS pacientes_adicionales_atendidos
FROM dias GROUP BY 1;

-- PN-07 Puntualidad (F3): distribución de minutos respecto a la hora de la cita
SELECT CASE WHEN minutes_from_scheduled < -15 THEN '>15 min antes'
            WHEN minutes_from_scheduled <= 0 THEN '0-15 min antes'
            WHEN minutes_from_scheduled <= 20 THEN 'dentro de gracia (<=20)'
            ELSE 'tarde (>20) = inasistencia RN-01' END AS franja,
       count(*) AS admisiones
FROM clinicared_gold.fact_admission WHERE is_unlinked = 0
GROUP BY 1 ORDER BY 1;

-- =============================================================================
-- Controles de calidad (QC) - deben devolver 0 filas / 0 conteo
-- =============================================================================
-- QC-01 Unicidad de la llave de negocio en el hecho
SELECT appointment_id, count(*) FROM clinicared_gold.fact_appointment GROUP BY 1 HAVING count(*) > 1;

-- QC-02 Integridad referencial: hechos sin paciente / sede / especialidad
SELECT
  sum(CASE WHEN p.patient_key IS NULL THEN 1 ELSE 0 END) AS sin_paciente,
  sum(CASE WHEN b.id IS NULL THEN 1 ELSE 0 END) AS sin_sede,
  sum(CASE WHEN s.id IS NULL THEN 1 ELSE 0 END) AS sin_especialidad
FROM clinicared_gold.fact_appointment f
LEFT JOIN clinicared_gold.dim_patient p ON p.patient_key = f.patient_key
LEFT JOIN clinicared_gold.dim_branch b ON b.id = f.location_id
LEFT JOIN clinicared_gold.dim_specialty s ON s.id = f.specialty_id;

-- QC-03 RN-09: ninguna columna sensible en la base analítica
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'clinicared_gold'
  AND regexp_like(lower(column_name), '(national_id|first_name|last_name|phone|email|chronic_conditions|birth|address)');

-- QC-04 Admisiones F3 sin cita enlazada (debe ser < 2 %)
SELECT round(100.0 * avg(is_unlinked), 2) AS pct_no_enlazadas FROM clinicared_gold.fact_admission;

-- QC-05 Coherencia RN-01: asistencias registradas fuera de la gracia deben ser NO_SHOW
SELECT count(*) FROM clinicared_gold.fact_admission a
JOIN clinicared_gold.fact_appointment f ON f.appointment_id = a.appointment_id
WHERE a.minutes_from_scheduled > 20 AND f.outcome = 'ATTENDED'
  AND NOT EXISTS (SELECT 1 FROM clinicared_gold.fact_admission a2
                  WHERE a2.appointment_id = a.appointment_id AND a2.minutes_from_scheduled <= 20);
