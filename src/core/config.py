"""Application settings.

Secrets have no default values: they must come from the environment (local
``.env`` file, never committed) or, on AWS, from Secrets Manager injected by
ECS as environment variables. Startup fails fast if a secret is missing.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "ClínicaRed Backend API"
    VERSION: str = "2.0.0"
    ENVIRONMENT: str = "local"
    LOG_LEVEL: str = "INFO"

    # PostgreSQL / RDS
    DB_USER: str = "clinicared_app"
    DB_PASSWORD: SecretStr
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "clinicared_db"
    DB_SSLMODE: str = "prefer"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 10

    # RabbitMQ / Amazon MQ (amqps:// in AWS)
    RABBITMQ_URL: SecretStr = SecretStr("amqp://guest:guest@localhost:5672/%2F")
    RABBITMQ_PREFETCH: int = 20

    # Messaging provider
    MESSAGING_GATEWAY_URL: str = "http://localhost:8000/simulate/messaging/send"
    MESSAGING_GATEWAY_TOKEN: SecretStr = SecretStr("")
    MESSAGING_TIMEOUT_SECONDS: float = 5.0
    WEBHOOK_SIGNING_SECRET: SecretStr

    # RN-09
    PSEUDONYMIZATION_PEPPER: SecretStr = Field(default=SecretStr(""))

    # RN-05
    OVERBOOK_BLOCK_MINUTES: int = 60

    # Observability
    METRICS_NAMESPACE: str = "ClinicaRed"

    @property
    def DATABASE_URL(self) -> str:  # noqa: N802 - kept for backwards compatibility
        """SQLAlchemy URL built from the individual settings."""
        password = quote_plus(self.DB_PASSWORD.get_secret_value())
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{password}@{self.DB_HOST}:{self.DB_PORT}/"
            f"{self.DB_NAME}?sslmode={self.DB_SSLMODE}"
        )


settings = Settings()  # type: ignore[call-arg]
