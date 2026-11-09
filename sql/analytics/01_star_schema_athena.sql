-- =============================================================================
-- Modelo estrella (capa Gold) expuesto en Amazon Athena para Power BI.
-- Ubicación: s3://<lake>/gold/<tabla>/  · Formato: Parquet (snappy)
-- Hechos particionados por year_month (partition projection: sin MSCK REPAIR).
-- RN-09: ninguna tabla contiene nombres, documento, contacto ni diagnósticos.
-- Reemplace ${LAKE} por el bucket (p.ej. con `sed` en el pipeline de despliegue).
-- =============================================================================
CREATE DATABASE IF NOT EXISTS clinicared_gold;

CREATE EXTERNAL TABLE IF NOT EXISTS clinicared_gold.dim_patient (
    patient_key           string  COMMENT 'sha256(pepper|documento normalizado) - seudónimo RN-09',
    age_band              string  COMMENT '0-17,18-29,30-44,45-59,60-74,75+,UNKNOWN',
    is_minor              boolean,
    sex                   string,
    city                  string,
    has_chronic_condition boolean COMMENT 'Bandera agregada; el diagnóstico nunca sale de OLTP'
) STORED AS PARQUET LOCATION 's3://${LAKE}/gold/dim_patient/';

CREATE EXTERNAL TABLE IF NOT EXISTS clinicared_gold.dim_branch (
    id int, name string, city string, code string
) STORED AS PARQUET LOCATION 's3://${LAKE}/gold/dim_branch/';

CREATE EXTERNAL TABLE IF NOT EXISTS clinicared_gold.dim_specialty (
    id int, name string, is_overbookable boolean COMMENT 'F7'
) STORED AS PARQUET LOCATION 's3://${LAKE}/gold/dim_specialty/';

CREATE EXTERNAL TABLE IF NOT EXISTS clinicared_gold.dim_professional (
    id int, is_active boolean
) STORED AS PARQUET LOCATION 's3://${LAKE}/gold/dim_professional/';

CREATE EXTERNAL TABLE IF NOT EXISTS clinicared_gold.dim_date (
    date_key int, `date` date, year int, month int, day int, year_month string,
    iso_weekday int, is_weekend boolean, is_holiday boolean, holiday_name string
) STORED AS PARQUET LOCATION 's3://${LAKE}/gold/dim_date/';

CREATE EXTERNAL TABLE IF NOT EXISTS clinicared_gold.fact_appointment (
    appointment_id int, patient_key string, professional_id int, location_id int, specialty_id int,
    date_key int, scheduled_hour int, lead_time_days int, risk_level string, is_overbook boolean,
    booking_channel string, was_confirmed boolean, outcome string, is_no_show int, is_attended int,
    is_cancelled_on_time int, reminders_sent bigint, reminder_cost_cop decimal(12,2),
    precipitation_mm decimal(6,1), weather_condition string, is_severe_weather boolean
)
PARTITIONED BY (year_month string)
STORED AS PARQUET LOCATION 's3://${LAKE}/gold/fact_appointment/'
TBLPROPERTIES (
  'projection.enabled'='true', 'projection.year_month.type'='date', 'projection.year_month.format'='yyyy-MM',
  'projection.year_month.range'='2024-01,NOW+1MONTHS', 'projection.year_month.interval'='1',
  'projection.year_month.interval.unit'='MONTHS',
  'storage.location.template'='s3://${LAKE}/gold/fact_appointment/year_month=${year_month}/'
);

CREATE EXTERNAL TABLE IF NOT EXISTS clinicared_gold.fact_reminder (
    message_id int, appointment_id int, patient_key string, location_id int, specialty_id int, date_key int,
    reminder_kind string, channel string, recipient_type string, risk_level string, status string,
    is_sent int, is_delivered int, cost_cop decimal(10,2), hours_before_appointment double
)
PARTITIONED BY (year_month string)
STORED AS PARQUET LOCATION 's3://${LAKE}/gold/fact_reminder/'
TBLPROPERTIES (
  'projection.enabled'='true', 'projection.year_month.type'='date', 'projection.year_month.format'='yyyy-MM',
  'projection.year_month.range'='2024-01,NOW+1MONTHS', 'projection.year_month.interval'='1',
  'projection.year_month.interval.unit'='MONTHS',
  'storage.location.template'='s3://${LAKE}/gold/fact_reminder/year_month=${year_month}/'
);

CREATE EXTERNAL TABLE IF NOT EXISTS clinicared_gold.fact_admission (
    admission_id string, appointment_id int, patient_key string, location_id int, specialty_id int,
    date_key int, minutes_from_scheduled double, within_grace int, is_unlinked int
)
PARTITIONED BY (year_month string)
STORED AS PARQUET LOCATION 's3://${LAKE}/gold/fact_admission/'
TBLPROPERTIES (
  'projection.enabled'='true', 'projection.year_month.type'='date', 'projection.year_month.format'='yyyy-MM',
  'projection.year_month.range'='2024-01,NOW+1MONTHS', 'projection.year_month.interval'='1',
  'projection.year_month.interval.unit'='MONTHS',
  'storage.location.template'='s3://${LAKE}/gold/fact_admission/year_month=${year_month}/'
);

CREATE EXTERNAL TABLE IF NOT EXISTS clinicared_gold.agg_occupancy_daily (
    location_id int, specialty_id int, date_key int, regular_capacity bigint, booked_regular bigint,
    attended_regular bigint, attended_overbook bigint, no_shows bigint,
    effective_occupancy double, nominal_occupancy double, lost_capacity bigint
)
PARTITIONED BY (year_month string)
STORED AS PARQUET LOCATION 's3://${LAKE}/gold/agg_occupancy_daily/'
TBLPROPERTIES (
  'projection.enabled'='true', 'projection.year_month.type'='date', 'projection.year_month.format'='yyyy-MM',
  'projection.year_month.range'='2024-01,NOW+1MONTHS', 'projection.year_month.interval'='1',
  'projection.year_month.interval.unit'='MONTHS',
  'storage.location.template'='s3://${LAKE}/gold/agg_occupancy_daily/year_month=${year_month}/'
);
