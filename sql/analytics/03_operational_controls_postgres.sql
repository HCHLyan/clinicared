-- =============================================================================
-- Controles operativos sobre PostgreSQL (OLTP) - ejecutables por clinicared_auditor.
-- =============================================================================

-- OC-01 RN-06: latencia de liberación de cupos (últimos 7 días); p95 debe ser <= 600 s
SELECT count(*) AS ofertas,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY release_latency_seconds) AS p95_segundos,
       max(release_latency_seconds) AS max_segundos,
       count(*) FILTER (WHERE release_latency_seconds > 600) AS fuera_de_sla
FROM waitlist_offers WHERE offered_at >= now() - interval '7 days';

-- OC-02 Idempotencia: ningún mensaje enviado dos veces para la misma cita/tipo/versión/destinatario
SELECT appointment_id, message_type, appointment_version, recipient_type, count(*)
FROM messages WHERE status IN ('SENT', 'DELIVERED', 'READ')
GROUP BY 1, 2, 3, 4 HAVING count(*) > 1;

-- OC-03 RN-04: ningún envío en horario silencioso ni festivo
SELECT id, sent_at FROM messages m
WHERE m.sent_at IS NOT NULL
  AND (m.sent_at::time >= '20:00' OR m.sent_at::time < '07:00'
       OR EXISTS (SELECT 1 FROM holidays h WHERE h.holiday_date = m.sent_at::date));

-- OC-04 RN-11: ningún mensaje a un menor dirigido al paciente
SELECT m.id FROM messages m
JOIN appointments a ON a.id = m.appointment_id
JOIN patients p ON p.id = m.patient_id
WHERE m.recipient_type = 'PATIENT' AND m.message_type IN ('H72', 'H24')
  AND p.date_of_birth > (a.scheduled_datetime::date - interval '18 years');

-- OC-05 RN-05: ningún bloque con sobrecupo por encima de lo permitido
SELECT a.professional_id, date_trunc('hour', a.scheduled_datetime) AS bloque,
       count(*) FILTER (WHERE a.is_overbook) AS usados, max(oc.allowed_extra) AS permitidos
FROM appointments a
LEFT JOIN overbook_capacity oc ON oc.professional_id = a.professional_id AND oc.location_id = a.location_id
     AND oc.block_start = date_trunc('hour', a.scheduled_datetime)
WHERE a.status <> 'CANCELLED' AND a.scheduled_datetime >= now()
GROUP BY 1, 2 HAVING count(*) FILTER (WHERE a.is_overbook) > coalesce(max(oc.allowed_extra), 0);

-- OC-06 RN-10: integridad de la cadena de auditoría (vacío = íntegra)
SELECT * FROM audit_log_verify_chain();

-- OC-07 Mensajes atascados (insumo del runbook RB-03)
SELECT status, count(*), min(coalesce(enqueued_at, send_at)) AS mas_antiguo
FROM messages WHERE status IN ('ENQUEUED', 'SENDING') GROUP BY 1;
