"""Connection parameters for RabbitMQ / Amazon MQ."""

from __future__ import annotations

import ssl

import pika


def connection_parameters(url: str, *, heartbeat: int = 30, client_name: str = "clinicared") -> pika.URLParameters:
    """Build connection parameters from an AMQP URL.

    ``amqps://`` URLs (Amazon MQ) get TLS with certificate verification.

    Args:
        url: ``amqp[s]://user:pass@host:port/vhost``.
        heartbeat: Heartbeat seconds (detects half-open TCP connections).
        client_name: Shown in the RabbitMQ management UI.

    Returns:
        Configured parameters.
    """
    params = pika.URLParameters(url)
    params.heartbeat = heartbeat
    params.blocked_connection_timeout = 60
    params.connection_attempts = 5
    params.retry_delay = 3
    params.client_properties = {"connection_name": client_name}
    if url.startswith("amqps://"):
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        params.ssl_options = pika.SSLOptions(context, server_hostname=params.host)
    return params
