"""Pure DataFrame transformations (no I/O): unit-testable with a local session.

The rule constants mirror ``src/domain`` (RN-01, RN-08, RN-09). They are
duplicated deliberately because executors must not depend on the API
package; ``tests/unit/test_spark_parity.py`` guards that they stay equal.
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql.types import BooleanType, DateType, DecimalType, IntegerType, StringType

from clinicared_spark.schemas import CORRUPT_COLUMN

MIN_VALID_AGE = 0
MAX_VALID_AGE = 120
ADULT_AGE = 18
SEVERE_PRECIPITATION_MM = 30
NON_CHRONIC_TOKENS = ["", "none", "ninguna", "n/a", "na", "null"]


# ----------------------------------------------------------------------------
# Helpers shared with src/domain/privacy.py
# ----------------------------------------------------------------------------
def normalize_national_id(column: Column) -> Column:
    """Same rule as ``src.domain.privacy.normalize_national_id``."""
    return F.upper(F.regexp_replace(column, r"[^0-9A-Za-z]", ""))


def patient_key(national_id_norm: Column, pepper: str) -> Column:
    """``sha256(pepper|national_id_norm)`` with the native ``sha2`` (no Python UDF)."""
    return F.sha2(F.concat(F.lit(pepper), F.lit("|"), national_id_norm), 256)


def age_band(age: Column) -> Column:
    """Same bands as ``src.domain.privacy.AGE_BANDS``."""
    return (
        F.when(age.isNull() | (age < 0), "UNKNOWN")
        .when(age <= 17, "0-17")
        .when(age <= 29, "18-29")
        .when(age <= 44, "30-44")
        .when(age <= 59, "45-59")
        .when(age <= 74, "60-74")
        .when(age <= 200, "75+")
        .otherwise("UNKNOWN")
    )


def _parse_date(column: Column) -> Column:
    return F.coalesce(F.to_date(column, "yyyy-MM-dd"), F.to_date(column, "dd/MM/yyyy"))


# ----------------------------------------------------------------------------
# Silver: cleansing
# ----------------------------------------------------------------------------
def cleanse_patients_f1(raw: DataFrame, run_date: str) -> tuple[DataFrame, DataFrame]:
    """Cleanse F1: outlier/negative ages, impossible birth dates, dedup.

    Rules:
        * ``birth_date`` in the future or before 1900 -> NULL + ``BIRTH_DATE_INVALID``.
        * reported ``age`` < 0 or > 120 -> ignored + ``AGE_OUT_OF_RANGE``.
        * age is recomputed from ``birth_date`` when available; a reported age
          differing by more than 1 year -> ``AGE_MISMATCH`` (computed wins).
        * raw chronic conditions are reduced to a boolean here and never
          written to any analytical zone (RN-09).
        * duplicates by normalized national id: keep latest ``updated_at``.

    Returns:
        ``(clean, quarantine)``; quarantine holds unparseable rows and rows
        without a national id, with a ``reason`` column.
    """
    run = F.to_date(F.lit(run_date))
    staged = raw.withColumn("national_id_norm", normalize_national_id(F.col("national_id")))

    quarantine = staged.where(F.col(CORRUPT_COLUMN).isNotNull() | (F.coalesce(F.col("national_id_norm"), F.lit("")) == "")).select(
        F.coalesce(F.col(CORRUPT_COLUMN), F.to_json(F.struct("patient_id", "updated_at"))).alias("record"),
        F.when(F.col(CORRUPT_COLUMN).isNotNull(), "MALFORMED_ROW").otherwise("MISSING_NATIONAL_ID").alias("reason"),
        F.lit(run_date).alias("run_date"),
    )

    valid = staged.where(F.col(CORRUPT_COLUMN).isNull() & (F.col("national_id_norm") != ""))
    birth = _parse_date(F.col("birth_date"))
    birth_ok = birth.isNotNull() & (birth <= run) & (birth >= F.to_date(F.lit("1900-01-01")))
    reported_age = F.col("age").cast(IntegerType())
    reported_ok = reported_age.between(MIN_VALID_AGE, MAX_VALID_AGE)
    computed_age = F.floor(F.months_between(run, birth) / 12).cast(IntegerType())

    typed = (
        valid.withColumn("birth_date_clean", F.when(birth_ok, birth).cast(DateType()))
        .withColumn("computed_age", F.when(birth_ok, computed_age))
        .withColumn("reported_age_clean", F.when(reported_ok, reported_age))
        .withColumn("age_clean", F.coalesce(F.col("computed_age"), F.col("reported_age_clean")))
        .withColumn(
            "dq_flags",
            F.concat_ws(
                ",",
                F.when(birth.isNotNull() & ~birth_ok, F.lit("BIRTH_DATE_INVALID")),
                F.when(reported_age.isNotNull() & ~reported_ok, F.lit("AGE_OUT_OF_RANGE")),
                F.when(
                    F.col("computed_age").isNotNull()
                    & F.col("reported_age_clean").isNotNull()
                    & (F.abs(F.col("computed_age") - F.col("reported_age_clean")) > 1),
                    F.lit("AGE_MISMATCH"),
                ),
            ),
        )
        .withColumn(
            "has_chronic_condition",
            ~F.lower(F.trim(F.coalesce(F.col("chronic_conditions"), F.lit("")))).isin(NON_CHRONIC_TOKENS),
        )
        .withColumn("sex_clean", F.when(F.upper(F.trim("sex")).isin("M", "F"), F.upper(F.trim("sex"))))
        .withColumn("updated_ts", F.to_timestamp("updated_at"))
    )
    latest = Window.partitionBy("national_id_norm").orderBy(F.col("updated_ts").desc_nulls_last(), F.col("patient_id").desc())
    clean = (
        typed.withColumn("_rn", F.row_number().over(latest))
        .where(F.col("_rn") == 1)
        .select(
            F.col("patient_id").cast(IntegerType()).alias("patient_id"),
            "national_id_norm",
            F.col("birth_date_clean").alias("birth_date"),
            F.col("age_clean").alias("age"),
            F.col("sex_clean").alias("sex"),
            F.initcap(F.trim("city")).alias("city"),
            F.col("has_chronic_condition").cast(BooleanType()),
            F.when(F.col("dq_flags") == "", None).otherwise(F.col("dq_flags")).alias("dq_flags"),
            F.col("updated_ts").alias("updated_at"),
        )
    )
    return clean, quarantine


def cleanse_weather_f6(raw: DataFrame) -> DataFrame:
    """Type F6, null out physical outliers, derive ``is_severe``, dedup by (city, date)."""
    precipitation = F.col("precipitation_mm").cast("double")
    temperature = F.col("max_temp_c").cast("double")
    condition = F.upper(F.trim("condition"))
    typed = raw.where(F.col(CORRUPT_COLUMN).isNull()).select(
        F.initcap(F.trim("city")).alias("city"),
        _parse_date(F.col("weather_date")).alias("weather_date"),
        F.when(precipitation >= 0, precipitation).cast(DecimalType(6, 1)).alias("precipitation_mm"),
        F.when(temperature.between(-5, 45), temperature).cast(DecimalType(4, 1)).alias("max_temp_c"),
        condition.alias("condition"),
    )
    typed = typed.withColumn(
        "is_severe",
        (F.coalesce(F.col("precipitation_mm"), F.lit(0)) >= SEVERE_PRECIPITATION_MM) | F.col("condition").isin("STORM", "TORMENTA"),
    )
    return typed.where(F.col("city").isNotNull() & F.col("weather_date").isNotNull()).dropDuplicates(["city", "weather_date"])


def cleanse_holidays_f5(raw: DataFrame) -> DataFrame:
    """Type F5 and dedup by date."""
    return (
        raw.where(F.col(CORRUPT_COLUMN).isNull())
        .select(_parse_date(F.col("holiday_date")).alias("holiday_date"), F.trim("name").cast(StringType()).alias("name"))
        .where(F.col("holiday_date").isNotNull())
        .dropDuplicates(["holiday_date"])
    )


def apply_f7_rules(specialties: DataFrame, f7_raw: DataFrame) -> DataFrame:
    """F7 overrides ``is_overbookable`` per specialty (F7 is the source of truth)."""
    rules = f7_raw.where(F.col(CORRUPT_COLUMN).isNull()).select(
        F.col("specialty_id").cast(IntegerType()).alias("sid"),
        F.lower(F.trim("is_overbookable")).isin("true", "1", "si", "sí", "yes").alias("f7_overbookable"),
    )
    return (
        specialties.join(F.broadcast(rules), specialties.id == rules.sid, "left")
        .select("id", "name", F.coalesce("f7_overbookable", "is_overbookable").alias("is_overbookable"))
    )


# ----------------------------------------------------------------------------
# Gold: dimensional model
# ----------------------------------------------------------------------------
def build_dim_patient(patients_db: DataFrame, f1_clean: DataFrame, pepper: str, run_date: str, broadcast: bool) -> DataFrame:
    """Pseudonymized patient dimension (RN-09): no names, ids, contacts or diagnoses."""
    run = F.to_date(F.lit(run_date))
    base = patients_db.withColumn("national_id_norm", normalize_national_id(F.col("national_id")))
    f1 = f1_clean.select(
        "national_id_norm",
        F.col("age").alias("f1_age"),
        F.col("sex").alias("f1_sex"),
        "city",
        "has_chronic_condition",
    )
    joined = base.join(F.broadcast(f1) if broadcast else f1, "national_id_norm", "left")
    db_age = F.floor(F.months_between(run, F.col("date_of_birth")) / 12).cast(IntegerType())
    age = F.coalesce(F.when(db_age.between(0, 120), db_age), F.col("f1_age"))
    return joined.select(
        patient_key(F.col("national_id_norm"), pepper).alias("patient_key"),
        F.col("patient_id"),  # surrogate join key; dropped from facts after keying
        age_band(age).alias("age_band"),
        (age < ADULT_AGE).alias("is_minor"),
        F.coalesce(F.upper(F.col("sex")), F.col("f1_sex")).alias("sex"),
        F.coalesce(F.col("city"), F.lit("Desconocida")).alias("city"),
        F.coalesce(F.col("has_chronic_condition"), F.lit(False)).alias("has_chronic_condition"),
    )


def build_dim_date(spark_df_with_dates: DataFrame, holidays: DataFrame, date_column: str) -> DataFrame:
    """Date dimension covering every date that appears in the facts."""
    dates = spark_df_with_dates.select(F.col(date_column).alias("date")).where(F.col("date").isNotNull()).distinct()
    return (
        dates.join(F.broadcast(holidays), dates.date == holidays.holiday_date, "left")
        .select(
            F.date_format("date", "yyyyMMdd").cast(IntegerType()).alias("date_key"),
            "date",
            F.year("date").alias("year"),
            F.month("date").alias("month"),
            F.dayofmonth("date").alias("day"),
            F.date_format("date", "yyyy-MM").alias("year_month"),
            ((F.dayofweek("date") + 5) % 7 + 1).alias("iso_weekday"),
            F.dayofweek("date").isin(1, 7).alias("is_weekend"),
            F.col("holiday_date").isNotNull().alias("is_holiday"),
            F.col("name").alias("holiday_name"),
        )
    )


def appointment_outcome(appointments: DataFrame, attendances: DataFrame) -> DataFrame:
    """Attach the RN-01 outcome already computed by the admissions DAG."""
    outcomes = attendances.select(F.col("appointment_id").alias("att_appointment_id"), F.col("status").alias("outcome"))
    return appointments.join(outcomes, appointments.id == outcomes.att_appointment_id, "left").drop("att_appointment_id").withColumn(
        "outcome",
        F.coalesce(
            F.col("outcome"),
            F.when(F.col("status") == "CANCELLED", "CANCELLED_UNEVALUATED").otherwise("PENDING_EVALUATION"),
        ),
    )


def build_fact_appointment(
    appointments_with_outcome: DataFrame,
    risk: DataFrame,
    messages: DataFrame,
    dim_patient: DataFrame,
    locations: DataFrame,
    weather: DataFrame,
    broadcast: bool,
) -> DataFrame:
    """Grain: one row per appointment."""

    def b(df: DataFrame) -> DataFrame:
        return F.broadcast(df) if broadcast else df

    reminders = messages.where(F.col("message_type").isin("H72", "H24")).groupBy("appointment_id").agg(
        F.sum(F.when(F.col("status").isin("SENT", "DELIVERED", "READ"), 1).otherwise(0)).alias("reminders_sent"),
        F.sum(F.coalesce(F.col("cost_cop"), F.lit(0))).cast(DecimalType(12, 2)).alias("reminder_cost_cop"),
    )
    ap = appointments_with_outcome.alias("ap")
    joined = (
        ap.join(risk.alias("r"), F.col("ap.id") == F.col("r.appointment_id"), "left")
        .join(reminders.alias("m"), F.col("ap.id") == F.col("m.appointment_id"), "left")
        .join(
            b(dim_patient.select(F.col("patient_id").alias("dp_patient_id"), "patient_key")).alias("p"),
            F.col("ap.patient_id") == F.col("p.dp_patient_id"),
            "left",
        )
        .join(b(locations.select(F.col("id").alias("loc_id"), "city")).alias("l"), F.col("ap.location_id") == F.col("l.loc_id"), "left")
    )
    with_weather = joined.join(
        b(weather).alias("w"),
        (F.col("l.city") == F.col("w.city")) & (F.to_date("ap.scheduled_datetime") == F.col("w.weather_date")),
        "left",
    )
    lead = F.datediff(F.to_date("ap.scheduled_datetime"), F.to_date("ap.created_at"))
    return with_weather.select(
        F.col("ap.id").alias("appointment_id"),
        F.col("p.patient_key"),
        F.col("ap.professional_id"),
        F.col("ap.location_id"),
        F.col("ap.specialty_id"),
        F.date_format("ap.scheduled_datetime", "yyyyMMdd").cast(IntegerType()).alias("date_key"),
        F.hour("ap.scheduled_datetime").alias("scheduled_hour"),
        F.coalesce(F.col("r.lead_time_days"), F.greatest(lead, F.lit(0))).alias("lead_time_days"),
        F.coalesce(F.col("r.risk_level"), F.lit("UNSCORED")).alias("risk_level"),
        F.col("ap.is_overbook"),
        F.col("ap.booking_channel"),
        F.col("ap.confirmed_at").isNotNull().alias("was_confirmed"),
        F.col("ap.outcome"),
        (F.col("ap.outcome") == "NO_SHOW").cast(IntegerType()).alias("is_no_show"),
        (F.col("ap.outcome") == "ATTENDED").cast(IntegerType()).alias("is_attended"),
        (F.col("ap.outcome") == "CANCELLED_ON_TIME").cast(IntegerType()).alias("is_cancelled_on_time"),
        F.coalesce(F.col("m.reminders_sent"), F.lit(0)).alias("reminders_sent"),
        F.coalesce(F.col("m.reminder_cost_cop"), F.lit(0).cast(DecimalType(12, 2))).alias("reminder_cost_cop"),
        F.col("w.precipitation_mm"),
        F.coalesce(F.col("w.condition"), F.lit("SIN_DATO")).alias("weather_condition"),
        F.coalesce(F.col("w.is_severe"), F.lit(False)).alias("is_severe_weather"),
        F.date_format("ap.scheduled_datetime", "yyyy-MM").alias("year_month"),
    )


def build_fact_reminder(messages: DataFrame, appointments: DataFrame, dim_patient: DataFrame, broadcast: bool) -> DataFrame:
    """Grain: one row per outbound message."""
    patients = dim_patient.select("patient_id", "patient_key")
    ap = appointments.select(F.col("id").alias("ap_id"), "patient_id", "location_id", "specialty_id", "scheduled_datetime")
    joined = messages.join(ap, messages.appointment_id == ap.ap_id, "left").join(
        F.broadcast(patients) if broadcast else patients, "patient_id", "left"
    )
    event_ts = F.coalesce(F.col("sent_at"), F.col("send_at"))
    return joined.select(
        F.col("id").alias("message_id"),
        "appointment_id",
        "patient_key",
        "location_id",
        "specialty_id",
        F.date_format(event_ts, "yyyyMMdd").cast(IntegerType()).alias("date_key"),
        F.col("message_type").alias("reminder_kind"),
        "channel",
        "recipient_type",
        F.coalesce(F.col("risk_level"), F.lit("NA")).alias("risk_level"),
        "status",
        F.col("status").isin("SENT", "DELIVERED", "READ").cast(IntegerType()).alias("is_sent"),
        F.col("status").isin("DELIVERED", "READ").cast(IntegerType()).alias("is_delivered"),
        F.coalesce(F.col("cost_cop"), F.lit(0).cast(DecimalType(10, 2))).alias("cost_cop"),
        ((F.unix_timestamp("scheduled_datetime") - F.unix_timestamp(event_ts)) / 3600).cast("double").alias("hours_before_appointment"),
        F.date_format(event_ts, "yyyy-MM").alias("year_month"),
    )


def build_fact_admission(admissions: DataFrame, appointments: DataFrame, dim_patient: DataFrame, broadcast: bool) -> DataFrame:
    """Grain: one row per F3 admission (punctuality analysis)."""
    patients = dim_patient.select("patient_id", "patient_key")
    ap = appointments.select(F.col("id").alias("ap_id"), "patient_id", "location_id", "specialty_id", "scheduled_datetime")
    joined = admissions.join(ap, admissions.appointment_id == ap.ap_id, "left").join(
        F.broadcast(patients) if broadcast else patients, "patient_id", "left"
    )
    minutes_late = ((F.unix_timestamp("admitted_at") - F.unix_timestamp("scheduled_datetime")) / 60).cast("double")
    return joined.select(
        "admission_id",
        "appointment_id",
        "patient_key",
        "location_id",
        "specialty_id",
        F.date_format("admitted_at", "yyyyMMdd").cast(IntegerType()).alias("date_key"),
        minutes_late.alias("minutes_from_scheduled"),
        (F.col("appointment_id").isNotNull() & (minutes_late <= 20)).cast(IntegerType()).alias("within_grace"),
        F.col("appointment_id").isNull().cast(IntegerType()).alias("is_unlinked"),
        F.date_format("admitted_at", "yyyy-MM").alias("year_month"),
    )


def build_agg_occupancy_daily(fact_appointment: DataFrame, capacity: DataFrame) -> DataFrame:
    """RN-08 per branch / specialty / day (``effective = attended / regular_capacity``)."""
    usage = fact_appointment.groupBy("location_id", "specialty_id", "date_key", "year_month").agg(
        F.sum(
            F.when(~F.col("is_overbook") & ~F.col("outcome").isin("CANCELLED_ON_TIME", "CANCELLED_UNEVALUATED"), 1).otherwise(0)
        ).alias("booked_regular"),
        F.sum(F.when(~F.col("is_overbook"), F.col("is_attended")).otherwise(0)).alias("attended_regular"),
        F.sum(F.when(F.col("is_overbook"), F.col("is_attended")).otherwise(0)).alias("attended_overbook"),
        F.sum("is_no_show").alias("no_shows"),
    )
    cap = capacity.groupBy(
        "location_id", "specialty_id", F.date_format("agenda_date", "yyyyMMdd").cast(IntegerType()).alias("date_key")
    ).agg(F.sum("regular_slots").alias("regular_capacity"))
    joined = usage.join(cap, ["location_id", "specialty_id", "date_key"], "full_outer").fillna(
        0, subset=["booked_regular", "attended_regular", "attended_overbook", "no_shows", "regular_capacity"]
    )
    ratio = lambda num: F.when(F.col("regular_capacity") > 0, F.round(num / F.col("regular_capacity"), 4))  # noqa: E731
    return joined.select(
        "location_id",
        "specialty_id",
        "date_key",
        F.coalesce(F.col("year_month"), F.concat_ws("-", F.substring(F.col("date_key").cast("string"), 1, 4), F.substring(F.col("date_key").cast("string"), 5, 2))).alias("year_month"),
        "regular_capacity",
        "booked_regular",
        "attended_regular",
        "attended_overbook",
        "no_shows",
        ratio(F.col("attended_regular") + F.col("attended_overbook")).alias("effective_occupancy"),
        ratio(F.col("booked_regular")).alias("nominal_occupancy"),
        F.greatest(F.col("regular_capacity") - F.col("attended_regular"), F.lit(0)).alias("lost_capacity"),
    )
