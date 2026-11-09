-- =============================================================================
-- US 04 - Mensajería, orquestación, reglas de negocio, idempotencia y auditoría
-- Script 100 % idempotente: puede ejecutarse N veces sin efectos secundarios.
-- Hora local: TIMESTAMP WITHOUT TIME ZONE en America/Bogota (UTC-5, sin DST).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Catálogos y fuentes externas (F5 festivos, F6 clima, F7 sobrecupo)
-- ---------------------------------------------------------------------------
ALTER TABLE specialties ADD COLUMN IF NOT EXISTS is_overbookable BOOLEAN NOT NULL DEFAULT TRUE;   -- F7
ALTER TABLE locations   ADD COLUMN IF NOT EXISTS code VARCHAR(20);
CREATE UNIQUE INDEX IF NOT EXISTS ux_locations_code ON locations(code) WHERE code IS NOT NULL;

CREATE TABLE IF NOT EXISTS holidays (                       -- F5
    holiday_date DATE PRIMARY KEY,
    name         VARCHAR(120) NOT NULL,
    source       VARCHAR(20)  NOT NULL DEFAULT 'F5',
    loaded_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS weather_daily (                  -- F6
    city             VARCHAR(100) NOT NULL,
    weather_date     DATE         NOT NULL,
    precipitation_mm NUMERIC(6,1),
    max_temp_c       NUMERIC(4,1),
    condition        VARCHAR(40),
    is_severe        BOOLEAN      NOT NULL DEFAULT FALSE,
    loaded_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (city, weather_date)
);

-- ---------------------------------------------------------------------------
-- Pacientes: acudientes (RN-11) y datos clínicos sensibles (RN-09)
-- ---------------------------------------------------------------------------
ALTER TABLE patients ADD COLUMN IF NOT EXISTS sex CHAR(1);
ALTER TABLE patients ADD COLUMN IF NOT EXISTS chronic_conditions TEXT;           -- nunca sale de OLTP/silver
ALTER TABLE patients ADD COLUMN IF NOT EXISTS preferred_channel VARCHAR(10) NOT NULL DEFAULT 'SMS';

CREATE TABLE IF NOT EXISTS guardians (
    id           SERIAL PRIMARY KEY,
    patient_id   INT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    full_name    VARCHAR(200) NOT NULL,
    relationship VARCHAR(40)  NOT NULL,
    phone        VARCHAR(20),
    email        VARCHAR(150),
    is_primary   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_guardian_contact CHECK (phone IS NOT NULL OR email IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_guardians_primary ON guardians(patient_id) WHERE is_primary;

-- ---------------------------------------------------------------------------
-- Citas: confirmación, cancelación, sobrecupo y versión (orden de eventos)
-- ---------------------------------------------------------------------------
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS confirmed_at   TIMESTAMP;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS cancelled_at   TIMESTAMP;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS is_overbook    BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS booking_channel VARCHAR(20) NOT NULL DEFAULT 'SELF_SERVICE';
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS version        INT NOT NULL DEFAULT 1;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS outcome_reason VARCHAR(40);
DO $$ BEGIN
    ALTER TABLE appointments ADD CONSTRAINT ck_appointments_status
        CHECK (status IN ('PENDING','CONFIRMED','CANCELLED','ATTENDED','NO_SHOW'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS ix_appointments_patient_sched ON appointments(patient_id, scheduled_datetime);
CREATE INDEX IF NOT EXISTS ix_appointments_sched_status  ON appointments(scheduled_datetime, status);
CREATE INDEX IF NOT EXISTS ix_appointments_prof_sched    ON appointments(professional_id, scheduled_datetime);

-- Admisiones (F3): check-in real del paciente en la sede.
CREATE TABLE IF NOT EXISTS admissions (
    admission_id   VARCHAR(64) PRIMARY KEY,                 -- id natural de F3 => upsert idempotente
    appointment_id INT REFERENCES appointments(id),
    national_id    VARCHAR(50) NOT NULL,
    location_code  VARCHAR(20),
    admitted_at    TIMESTAMP NOT NULL,
    source_file    VARCHAR(255) NOT NULL,
    loaded_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_admissions_appointment ON admissions(appointment_id);
-- Enlace F3 -> paciente por documento normalizado (misma regla que src/domain/privacy.py).
CREATE INDEX IF NOT EXISTS ix_patients_norm_national_id
    ON patients ((regexp_replace(upper(national_id), '[^0-9A-Z]', '', 'g')));
CREATE INDEX IF NOT EXISTS ix_admissions_date ON admissions((admitted_at::date));

-- Riesgo RN-02 (una fila vigente por cita; se recalcula a diario).
CREATE TABLE IF NOT EXISTS risk_scores (
    appointment_id   INT PRIMARY KEY REFERENCES appointments(id) ON DELETE CASCADE,
    risk_level       VARCHAR(10) NOT NULL CHECK (risk_level IN ('HIGH','MEDIUM','LOW')),
    reasons          TEXT[] NOT NULL,
    no_shows_6m      INT NOT NULL,
    lead_time_days   INT NOT NULL,
    calculated_at    TIMESTAMP NOT NULL,
    calc_run_id      VARCHAR(250) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_risk_scores_level ON risk_scores(risk_level);

-- Capacidad de sobrecupo RN-05 por bloque de agenda.
CREATE TABLE IF NOT EXISTS overbook_capacity (
    professional_id    INT NOT NULL REFERENCES professionals(id),
    location_id        INT NOT NULL REFERENCES locations(id),
    block_start        TIMESTAMP NOT NULL,
    specialty_id       INT NOT NULL REFERENCES specialties(id),
    high_risk_count    INT NOT NULL,
    allowed_extra      INT NOT NULL CHECK (allowed_extra BETWEEN 0 AND 2),
    calculated_at      TIMESTAMP NOT NULL,
    PRIMARY KEY (professional_id, location_id, block_start)
);

-- Capacidad regular ofertada (RN-08). Fuente: sistema de agendas.
CREATE TABLE IF NOT EXISTS agenda_capacity (
    professional_id INT NOT NULL REFERENCES professionals(id),
    location_id     INT NOT NULL REFERENCES locations(id),
    specialty_id    INT NOT NULL REFERENCES specialties(id),
    agenda_date     DATE NOT NULL,
    regular_slots   INT NOT NULL CHECK (regular_slots >= 0),
    PRIMARY KEY (professional_id, location_id, agenda_date)
);

-- ---------------------------------------------------------------------------
-- Mensajes: se extiende la tabla existente como máquina de estados idempotente
-- ---------------------------------------------------------------------------
ALTER TABLE messages ADD COLUMN IF NOT EXISTS idempotency_key     VARCHAR(80);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS appointment_version INT NOT NULL DEFAULT 1;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS recipient_type      VARCHAR(10) NOT NULL DEFAULT 'PATIENT';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS channel             VARCHAR(10) NOT NULL DEFAULT 'SMS';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS recipient_address   VARCHAR(150);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS body                TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS risk_level          VARCHAR(10);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS send_at             TIMESTAMP;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS priority            SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS attempts            INT NOT NULL DEFAULT 0;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS lease_expires_at    TIMESTAMP;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS enqueued_at         TIMESTAMP;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS provider_message_id VARCHAR(100);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS delivery_status_at  TIMESTAMP;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS cost_cop            NUMERIC(10,2);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS skip_reason         VARCHAR(60);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS last_error          TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_idempotency_key ON messages(idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_provider_id     ON messages(provider_message_id) WHERE provider_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_messages_due ON messages(send_at) WHERE status = 'PLANNED';
CREATE INDEX IF NOT EXISTS ix_messages_appointment ON messages(appointment_id, status);
DO $$ BEGIN
    ALTER TABLE messages ADD CONSTRAINT ck_messages_status CHECK (status IN
        ('PENDING','PLANNED','ENQUEUED','SENDING','SENT','DELIVERED','READ','FAILED','CANCELLED','SKIPPED'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Deduplicación de webhooks del proveedor (reintentos / desorden).
CREATE TABLE IF NOT EXISTS webhook_events (
    provider_event_id VARCHAR(100) PRIMARY KEY,
    event_type        VARCHAR(30) NOT NULL,
    provider_message_id VARCHAR(100),
    occurred_at       TIMESTAMP NOT NULL,
    received_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    outcome           VARCHAR(30) NOT NULL
);

-- Idempotencia de la API (cabecera Idempotency-Key en POST).
CREATE TABLE IF NOT EXISTS api_idempotency_keys (
    idempotency_key VARCHAR(100) NOT NULL,
    endpoint        VARCHAR(120) NOT NULL,
    request_hash    CHAR(64)     NOT NULL,
    status_code     INT          NOT NULL,
    response_body   JSONB        NOT NULL,
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (idempotency_key, endpoint)
);

-- Transactional outbox: eventos escritos en la misma transacción del negocio.
CREATE TABLE IF NOT EXISTS outbox_events (
    id           BIGSERIAL PRIMARY KEY,
    event_key    VARCHAR(80) NOT NULL UNIQUE,
    exchange     VARCHAR(100) NOT NULL,
    routing_key  VARCHAR(100) NOT NULL,
    priority     SMALLINT NOT NULL DEFAULT 0,
    payload      JSONB NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_outbox_unpublished ON outbox_events(id) WHERE published_at IS NULL;

-- ---------------------------------------------------------------------------
-- Lista de espera RN-06
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS waitlist (
    id           SERIAL PRIMARY KEY,
    patient_id   INT NOT NULL REFERENCES patients(id),
    specialty_id INT NOT NULL REFERENCES specialties(id),
    location_id  INT REFERENCES locations(id),
    priority     SMALLINT NOT NULL DEFAULT 0,
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status       VARCHAR(10) NOT NULL DEFAULT 'WAITING' CHECK (status IN ('WAITING','OFFERED','BOOKED','WITHDRAWN')),
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_waitlist_active ON waitlist(patient_id, specialty_id) WHERE status IN ('WAITING','OFFERED');
CREATE INDEX IF NOT EXISTS ix_waitlist_lookup ON waitlist(specialty_id, status, priority DESC, requested_at);

CREATE TABLE IF NOT EXISTS waitlist_offers (
    id                       SERIAL PRIMARY KEY,
    released_appointment_id  INT NOT NULL REFERENCES appointments(id),
    waitlist_id              INT NOT NULL REFERENCES waitlist(id),
    offered_at               TIMESTAMP NOT NULL,
    expires_at               TIMESTAMP NOT NULL,
    status                   VARCHAR(10) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACCEPTED','EXPIRED','DECLINED')),
    release_latency_seconds  NUMERIC(10,3) NOT NULL,
    accepted_appointment_id  INT REFERENCES appointments(id),
    UNIQUE (released_appointment_id, waitlist_id)
);
-- Como máximo una oferta abierta por cupo liberado.
CREATE UNIQUE INDEX IF NOT EXISTS ux_waitlist_offer_open ON waitlist_offers(released_appointment_id) WHERE status = 'OPEN';
-- El mensaje de oferta referencia la oferta (su validez depende de ella, no de la cita cancelada).
ALTER TABLE messages ADD COLUMN IF NOT EXISTS waitlist_offer_id INT REFERENCES waitlist_offers(id);

-- ---------------------------------------------------------------------------
-- RN-10: bitácora de auditoría inmutable con encadenamiento de hashes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    occurred_at   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor         VARCHAR(150) NOT NULL,
    action        VARCHAR(20)  NOT NULL,
    resource_type VARCHAR(40)  NOT NULL,
    resource_id   VARCHAR(80),
    request_id    VARCHAR(64)  NOT NULL,
    http_path     VARCHAR(255) NOT NULL,
    status_code   INT          NOT NULL,
    client_ip     VARCHAR(64),
    details       JSONB,
    prev_hash     CHAR(64),
    row_hash      CHAR(64)
);
CREATE INDEX IF NOT EXISTS ix_audit_resource ON audit_log(resource_type, resource_id, occurred_at);

CREATE OR REPLACE FUNCTION audit_log_chain_hash() RETURNS TRIGGER AS $$
DECLARE previous CHAR(64);
BEGIN
    -- Serializa inserciones para que la cadena sea lineal (volumen bajo: accesos API).
    PERFORM pg_advisory_xact_lock(hashtext('audit_log_chain'));
    SELECT row_hash INTO previous FROM audit_log ORDER BY id DESC LIMIT 1;
    NEW.prev_hash := COALESCE(previous, repeat('0', 64));
    NEW.row_hash := encode(sha256(convert_to(
        NEW.prev_hash || '|' || NEW.occurred_at::text || '|' || NEW.actor || '|' || NEW.action || '|' ||
        NEW.resource_type || '|' || COALESCE(NEW.resource_id, '') || '|' || NEW.request_id || '|' ||
        NEW.http_path || '|' || NEW.status_code::text || '|' || COALESCE(NEW.details::text, ''), 'UTF8')), 'hex');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_log_block_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log es inmutable (RN-10): % no permitido', TG_OP USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_log_hash ON audit_log;
CREATE TRIGGER trg_audit_log_hash BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_chain_hash();
DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;
CREATE TRIGGER trg_audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_block_mutation();
DROP TRIGGER IF EXISTS trg_audit_log_no_truncate ON audit_log;
CREATE TRIGGER trg_audit_log_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION audit_log_block_mutation();

-- Verificación de integridad de la cadena (devuelve filas corruptas; vacío = íntegra).
CREATE OR REPLACE FUNCTION audit_log_verify_chain() RETURNS TABLE(broken_id BIGINT) AS $$
    SELECT a.id FROM (
        SELECT id, prev_hash, LAG(row_hash) OVER (ORDER BY id) AS expected_prev FROM audit_log
    ) a WHERE a.expected_prev IS NOT NULL AND a.prev_hash <> a.expected_prev;
$$ LANGUAGE sql STABLE;

-- ---------------------------------------------------------------------------
-- Roles de mínimo privilegio (las contraseñas se asignan fuera de este script)
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clinicared_app') THEN CREATE ROLE clinicared_app NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clinicared_etl') THEN CREATE ROLE clinicared_etl NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clinicared_auditor') THEN CREATE ROLE clinicared_auditor NOLOGIN; END IF;
END $$;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO clinicared_app, clinicared_etl;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO clinicared_app, clinicared_etl;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM clinicared_app, clinicared_etl;
GRANT SELECT ON audit_log TO clinicared_auditor;
-- La capa analítica/ETL nunca lee diagnósticos crudos (RN-09).
REVOKE SELECT ON patients FROM clinicared_etl;
GRANT SELECT (id, national_id, date_of_birth, sex, created_at, updated_at) ON patients TO clinicared_etl;

-- Triggers updated_at para las tablas nuevas
DROP TRIGGER IF EXISTS trg_guardians_updated_at ON guardians;
CREATE TRIGGER trg_guardians_updated_at BEFORE UPDATE ON guardians FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
DROP TRIGGER IF EXISTS trg_waitlist_updated_at ON waitlist;
CREATE TRIGGER trg_waitlist_updated_at BEFORE UPDATE ON waitlist FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
