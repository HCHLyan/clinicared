"""Pure domain layer for ClínicaRed Citas.

Every module in this package is free of I/O (no database, broker, HTTP or
clock access). Business rules RN-01..RN-11 are expressed as deterministic
functions so that the API, the Airflow DAGs, the RabbitMQ workers and the
historical simulation all share a single source of truth.

Time convention: all datetimes are *naive* and expressed in the clinic's local
time (America/Bogota, UTC-5, no DST). This matches the operational schema,
which stores ``TIMESTAMP WITHOUT TIME ZONE`` values.
"""
