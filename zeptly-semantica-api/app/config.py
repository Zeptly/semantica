"""Environment-driven configuration.

Only variables this service actually reads are defined here. Configuration is
validated once at startup; an unsafe posture (e.g. no API key in production)
raises ConfigError and the process refuses to start.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

VALID_ENVS = ("production", "staging", "development", "test")
# Environments in which anonymous access may be enabled explicitly.
NON_PRODUCTION_ENVS = ("development", "test")
MIN_PRODUCTION_KEY_LENGTH = 32
DEFAULT_PORT = 8080
DEFAULT_GRAPH_NAME = "zeptly_semantica"
# "0.0.0.0": IPv4 (default; Railway environments created on/after 2025-10-16
# have dual-stack private networking, and healthchecks arrive over IPv4).
# "::": explicit dual-stack socket (IPV6_V6ONLY=0) for legacy IPv6-only
# Railway private networking.
BIND_HOSTS = ("0.0.0.0", "::")  # noqa: S104 - container ingress


class ConfigError(RuntimeError):
    """Raised when configuration is missing or unsafe."""


def _parse_bool(name: str, raw: Optional[str], default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean (true/false)")


def _parse_int(name: str, raw: Optional[str], default: int, lo: int, hi: int) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


def _parse_float(name: str, raw: Optional[str], default: float) -> float:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if not 0 < value <= 60:
        raise ConfigError(f"{name} must be > 0 and <= 60")
    return value


@dataclass(frozen=True)
class Settings:
    env: str
    api_key: Optional[str]
    allow_anonymous: bool
    falkordb_host: str
    falkordb_port: int
    falkordb_password: Optional[str]
    graph_name: str
    graph_timeout_seconds: float
    port: int
    bind_host: str = "0.0.0.0"  # noqa: S104 - container ingress

    @property
    def is_production_like(self) -> bool:
        return self.env not in NON_PRODUCTION_ENVS

    @property
    def auth_required(self) -> bool:
        return not self.allow_anonymous

    def __repr__(self) -> str:  # never render secrets
        return (
            f"Settings(env={self.env!r}, api_key={'<set>' if self.api_key else None}, "
            f"allow_anonymous={self.allow_anonymous}, falkordb_host={self.falkordb_host!r}, "
            f"falkordb_port={self.falkordb_port}, "
            f"falkordb_password={'<set>' if self.falkordb_password else None}, "
            f"graph_name={self.graph_name!r}, port={self.port}, "
            f"bind_host={self.bind_host!r})"
        )

    __str__ = __repr__

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> Settings:
        e = os.environ if environ is None else environ

        # Unset SEMANTICA_ENV is treated as production: fail safe, not open.
        env = (e.get("SEMANTICA_ENV") or "production").strip().lower()
        if env not in VALID_ENVS:
            raise ConfigError(f"SEMANTICA_ENV must be one of {', '.join(VALID_ENVS)}")

        api_key = e.get("SEMANTICA_API_KEY") or None
        if api_key is not None:
            api_key = api_key.strip() or None

        allow_anonymous = _parse_bool(
            "SEMANTICA_ALLOW_ANONYMOUS", e.get("SEMANTICA_ALLOW_ANONYMOUS"), False
        )

        if allow_anonymous and env not in NON_PRODUCTION_ENVS:
            raise ConfigError(
                "SEMANTICA_ALLOW_ANONYMOUS=true is only permitted when "
                "SEMANTICA_ENV is development or test"
            )
        if not allow_anonymous and not api_key:
            raise ConfigError("SEMANTICA_API_KEY is required (anonymous access is disabled)")
        if (
            env not in NON_PRODUCTION_ENVS
            and api_key is not None
            and len(api_key) < MIN_PRODUCTION_KEY_LENGTH
        ):
            raise ConfigError(
                f"SEMANTICA_API_KEY must be at least {MIN_PRODUCTION_KEY_LENGTH} "
                f"characters when SEMANTICA_ENV={env}"
            )

        host = (e.get("FALKORDB_HOST") or "localhost").strip()
        password = e.get("FALKORDB_PASSWORD") or None
        if password is not None:
            password = password.strip() or None
        if env not in NON_PRODUCTION_ENVS and not password:
            # falkordb-server runs with protected-mode off; without
            # --requirepass anything on the private network could read or
            # flush the graph.
            raise ConfigError(f"FALKORDB_PASSWORD is required when SEMANTICA_ENV={env}")
        graph_name = (e.get("FALKORDB_GRAPH") or DEFAULT_GRAPH_NAME).strip()
        if not graph_name.replace("_", "").isalnum():
            raise ConfigError("FALKORDB_GRAPH may contain only letters, digits and _")

        bind_host = (e.get("SEMANTICA_BIND_HOST") or BIND_HOSTS[0]).strip()
        if bind_host not in BIND_HOSTS:
            raise ConfigError("SEMANTICA_BIND_HOST must be 0.0.0.0 or ::")

        return cls(
            env=env,
            api_key=api_key,
            allow_anonymous=allow_anonymous,
            falkordb_host=host,
            falkordb_port=_parse_int("FALKORDB_PORT", e.get("FALKORDB_PORT"), 6379, 1, 65535),
            falkordb_password=password,
            graph_name=graph_name,
            graph_timeout_seconds=_parse_float(
                "FALKORDB_TIMEOUT_SECONDS", e.get("FALKORDB_TIMEOUT_SECONDS"), 5.0
            ),
            port=_parse_int("PORT", e.get("PORT"), DEFAULT_PORT, 1, 65535),
            bind_host=bind_host,
        )
