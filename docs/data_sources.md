# Contratos de fuentes de datos F1–F7

> ⚠️ El repositorio no incluía la especificación de F1–F7. Los contratos siguientes se **infirieron**
> a partir de las reglas de negocio y deben validarse con los dueños de cada fuente.

| Id | Fuente | Formato / ubicación | Frecuencia | Columnas | Consumidor |
|---|---|---|---|---|---|
| F1 | Maestro de pacientes (HIS) | CSV `landing/f1_patients/dt=YYYY-MM-DD/` | Diario | `patient_id, national_id, first_name, last_name, birth_date (yyyy-MM-dd o dd/MM/yyyy), age, sex, phone, email, chronic_conditions, city, updated_at` | `spark/jobs/cleanse.py` |
| F2 | Citas (OLTP) | PostgreSQL `appointments` | Continuo | Ver `01_init_schema.sql` + `02_us04_schema.sql` | API, DAG, Spark (JDBC) |
| F3 | Admisiones (check-in en sede) | CSV `landing/admissions/dt=YYYY-MM-DD/` | Diario (02:00) | `admission_id, appointment_id (opcional), national_id, location_code, admitted_at (ISO-8601)` | `admissions_consolidation_dag` |
| F4 | Recibos del proveedor de mensajería | Webhook firmado `POST /webhooks/messaging` | Tiempo real | `event_id, type (STATUS/REPLY), message_id, occurred_at, status, reply_text` | `webhook_service.py` |
| F5 | Festivos oficiales | CSV `landing/holidays/*.csv` | Anual | `holiday_date, name` | Calendario RN-04 (con respaldo legal en `holidays_co.py`) |
| F6 | Pronóstico del clima | CSV `landing/weather/dt=YYYY-MM-DD/` | Diario | `city, weather_date, precipitation_mm, max_temp_c, condition` | Texto del recordatorio, analítica |
| F7 | Reglas de sobrecupo por especialidad | CSV `landing/f7_specialty_rules/*.csv` | Bajo demanda | `specialty_id, specialty_name, is_overbookable` | RN-05 (`specialties.is_overbookable`) |

## Reglas de calidad por fuente
* **F1:** el documento es obligatorio (si falta, va a cuarentena). Edad fuera de [0, 120] produce
  `AGE_OUT_OF_RANGE`. Nacimiento futuro o anterior a 1900 produce `BIRTH_DATE_INVALID`. Sexo fuera
  de {M, F} se anula. Duplicados: gana el último `updated_at`.
* **F3:** una fila sin `admission_id`, documento o `admitted_at` válido se rechaza. Si el rechazo
  supera el 5 % del archivo, el DAG falla y alerta. Una admisión sin `appointment_id` se enlaza por
  documento y la cita más cercana dentro de ±3 h.
* **F6:** precipitación negativa o temperatura fuera de [−5, 45] °C se anulan.
* **F5/F7:** se deduplican por llave.
