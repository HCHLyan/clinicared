"""Pytest configuration.

Unit tests run without any infrastructure. Settings that are mandatory in
production get harmless test values *before* any ``src`` module is imported.
"""

from __future__ import annotations

import os

os.environ.setdefault("DB_PASSWORD", "test-only-password")
os.environ.setdefault("WEBHOOK_SIGNING_SECRET", "test-only-webhook-secret")
os.environ.setdefault("PSEUDONYMIZATION_PEPPER", "test-only-pepper-0123456789abcdef0123")
