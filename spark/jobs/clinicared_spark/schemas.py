"""Explicit schemas for every source. Schema inference is never used: it is
slow (extra pass over the data) and silently changes types when a file
contains a bad value.

File sources are read with all-string "raw" schemas plus a corrupt-record
column, then cast explicitly in the cleansing step so that bad values are
quarantined instead of becoming silent NULLs.
"""

from __future__ import annotations

from pyspark.sql.types import (
    BooleanType,
    DateType,
    DecimalType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

CORRUPT_COLUMN = "_corrupt_record"


def _raw(*names: str) -> StructType:
    return StructType([StructField(n, StringType(), True) for n in names] + [StructField(CORRUPT_COLUMN, StringType(), True)])


# ---- File sources (bronze/landing, CSV with header) -------------------------
F1_PATIENTS_RAW = _raw(
    "patient_id", "national_id", "first_name", "last_name", "birth_date", "age", "sex",
    "phone", "email", "chronic_conditions", "city", "updated_at",
)
F5_HOLIDAYS_RAW = _raw("holiday_date", "name")
F6_WEATHER_RAW = _raw("city", "weather_date", "precipitation_mm", "max_temp_c", "condition")
F7_SPECIALTY_RULES_RAW = _raw("specialty_id", "specialty_name", "is_overbookable")

# ---- Silver (typed) ---------------------------------------------------------
PATIENTS_SILVER = StructType([
    StructField("patient_id", IntegerType(), True),
    StructField("national_id_norm", StringType(), False),
    StructField("birth_date", DateType(), True),
    StructField("age", IntegerType(), True),
    StructField("sex", StringType(), True),
    StructField("city", StringType(), True),
    StructField("has_chronic_condition", BooleanType(), False),
    StructField("dq_flags", StringType(), True),
    StructField("updated_at", TimestampType(), True),
])

APPOINTMENTS_SILVER = StructType([
    StructField("id", IntegerType(), False),
    StructField("patient_id", IntegerType(), False),
    StructField("professional_id", IntegerType(), False),
    StructField("location_id", IntegerType(), False),
    StructField("specialty_id", IntegerType(), False),
    StructField("scheduled_datetime", TimestampType(), False),
    StructField("status", StringType(), False),
    StructField("created_at", TimestampType(), True),
    StructField("confirmed_at", TimestampType(), True),
    StructField("cancelled_at", TimestampType(), True),
    StructField("is_overbook", BooleanType(), False),
    StructField("booking_channel", StringType(), True),
])

ATTENDANCES_SILVER = StructType([
    StructField("appointment_id", IntegerType(), False),
    StructField("status", StringType(), False),
    StructField("check_in_time", TimestampType(), True),
])

MESSAGES_SILVER = StructType([
    StructField("id", IntegerType(), False),
    StructField("appointment_id", IntegerType(), False),
    StructField("message_type", StringType(), False),
    StructField("status", StringType(), False),
    StructField("channel", StringType(), True),
    StructField("recipient_type", StringType(), True),
    StructField("risk_level", StringType(), True),
    StructField("send_at", TimestampType(), True),
    StructField("sent_at", TimestampType(), True),
    StructField("cost_cop", DecimalType(10, 2), True),
])

RISK_SILVER = StructType([
    StructField("appointment_id", IntegerType(), False),
    StructField("risk_level", StringType(), False),
    StructField("lead_time_days", IntegerType(), True),
])

ADMISSIONS_SILVER = StructType([
    StructField("admission_id", StringType(), False),
    StructField("appointment_id", IntegerType(), True),
    StructField("admitted_at", TimestampType(), False),
])

LOCATIONS_SILVER = StructType([
    StructField("id", IntegerType(), False),
    StructField("name", StringType(), False),
    StructField("city", StringType(), False),
    StructField("code", StringType(), True),
])

SPECIALTIES_SILVER = StructType([
    StructField("id", IntegerType(), False),
    StructField("name", StringType(), False),
    StructField("is_overbookable", BooleanType(), False),
])

PROFESSIONALS_SILVER = StructType([
    StructField("id", IntegerType(), False),
    StructField("is_active", BooleanType(), True),
])

CAPACITY_SILVER = StructType([
    StructField("professional_id", IntegerType(), False),
    StructField("location_id", IntegerType(), False),
    StructField("specialty_id", IntegerType(), False),
    StructField("agenda_date", DateType(), False),
    StructField("regular_slots", IntegerType(), False),
])

HOLIDAYS_SILVER = StructType([
    StructField("holiday_date", DateType(), False),
    StructField("name", StringType(), False),
])

WEATHER_SILVER = StructType([
    StructField("city", StringType(), False),
    StructField("weather_date", DateType(), False),
    StructField("precipitation_mm", DecimalType(6, 1), True),
    StructField("max_temp_c", DecimalType(4, 1), True),
    StructField("condition", StringType(), True),
    StructField("is_severe", BooleanType(), False),
])

#: Columns extracted from PostgreSQL per table (least privilege: the ETL role
#: can only read these columns of ``patients``; no names, phones, diagnoses).
JDBC_EXTRACTS: dict[str, tuple[str, StructType]] = {
    "appointments": (
        "SELECT id, patient_id, professional_id, location_id, specialty_id, scheduled_datetime, status, created_at, "
        "confirmed_at, cancelled_at, is_overbook, booking_channel FROM appointments "
        "WHERE scheduled_datetime >= DATE '{window_start}'",
        APPOINTMENTS_SILVER,
    ),
    "attendances": (
        "SELECT at.appointment_id, at.status, at.check_in_time FROM attendances at "
        "JOIN appointments ap ON ap.id = at.appointment_id WHERE ap.scheduled_datetime >= DATE '{window_start}'",
        ATTENDANCES_SILVER,
    ),
    "messages": (
        "SELECT m.id, m.appointment_id, m.message_type, m.status, m.channel, m.recipient_type, m.risk_level, "
        "m.send_at, m.sent_at, m.cost_cop FROM messages m WHERE m.created_at >= DATE '{window_start}'",
        MESSAGES_SILVER,
    ),
    "risk_scores": ("SELECT appointment_id, risk_level, lead_time_days FROM risk_scores", RISK_SILVER),
    "admissions": (
        "SELECT admission_id, appointment_id, admitted_at FROM admissions WHERE admitted_at >= DATE '{window_start}'",
        ADMISSIONS_SILVER,
    ),
    "locations": ("SELECT id, name, city, code FROM locations", LOCATIONS_SILVER),
    "specialties": ("SELECT id, name, is_overbookable FROM specialties", SPECIALTIES_SILVER),
    "professionals": ("SELECT id, is_active FROM professionals", PROFESSIONALS_SILVER),
    "agenda_capacity": (
        "SELECT professional_id, location_id, specialty_id, agenda_date, regular_slots FROM agenda_capacity "
        "WHERE agenda_date >= DATE '{window_start}'",
        CAPACITY_SILVER,
    ),
    "patients_db": (
        "SELECT id AS patient_id, national_id, date_of_birth, sex FROM patients",
        StructType([
            StructField("patient_id", IntegerType(), False),
            StructField("national_id", StringType(), False),
            StructField("date_of_birth", DateType(), True),
            StructField("sex", StringType(), True),
        ]),
    ),
}

#: Tables large enough to justify parallel JDBC reads (``partitionColumn``).
JDBC_PARTITION_COLUMN: dict[str, str] = {"appointments": "id", "messages": "id"}
