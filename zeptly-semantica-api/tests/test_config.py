"""Unit tests for configuration posture (no graph store needed)."""

import pytest

from app.config import ConfigError, Settings

GOOD_KEY = "k" * 40
PROD = {"SEMANTICA_ENV": "production", "SEMANTICA_API_KEY": GOOD_KEY, "FALKORDB_PASSWORD": "pw"}


def test_unset_env_defaults_to_production_and_requires_key():
    with pytest.raises(ConfigError, match="SEMANTICA_API_KEY is required"):
        Settings.from_env({})


def test_production_with_key_is_valid_and_auth_required():
    s = Settings.from_env(PROD)
    assert s.auth_required and s.is_production_like
    assert s.port == 8080 and s.falkordb_port == 6379


def test_production_rejects_anonymous_even_with_key():
    with pytest.raises(ConfigError, match="only permitted"):
        Settings.from_env(
            {
                "SEMANTICA_ENV": "production",
                "SEMANTICA_API_KEY": GOOD_KEY,
                "SEMANTICA_ALLOW_ANONYMOUS": "true",
            }
        )


def test_staging_rejects_anonymous():
    with pytest.raises(ConfigError):
        Settings.from_env({"SEMANTICA_ENV": "staging", "SEMANTICA_ALLOW_ANONYMOUS": "true"})


def test_production_rejects_short_key():
    with pytest.raises(ConfigError, match="at least 32"):
        Settings.from_env({"SEMANTICA_ENV": "production", "SEMANTICA_API_KEY": "short"})


def test_blank_key_is_missing():
    with pytest.raises(ConfigError, match="required"):
        Settings.from_env({"SEMANTICA_ENV": "development", "SEMANTICA_API_KEY": "   "})


def test_development_may_allow_anonymous_explicitly():
    s = Settings.from_env({"SEMANTICA_ENV": "development", "SEMANTICA_ALLOW_ANONYMOUS": "true"})
    assert not s.auth_required


@pytest.mark.parametrize(
    "bad",
    [
        {"SEMANTICA_ENV": "prod"},
        {"PORT": "abc"},
        {"PORT": "0"},
        {"FALKORDB_PORT": "70000"},
        {"SEMANTICA_ALLOW_ANONYMOUS": "maybe"},
        {"FALKORDB_GRAPH": "x;DROP"},
    ],
)
def test_invalid_values_rejected(bad):
    Settings.from_env(PROD)  # the base environment itself is valid
    env = {**PROD, **bad}
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_railway_port_is_honoured():
    s = Settings.from_env({**PROD, "PORT": "4321"})
    assert s.port == 4321


def test_repr_never_contains_secrets():
    s = Settings.from_env({**PROD, "FALKORDB_PASSWORD": "hunter2-secret"})
    assert GOOD_KEY not in repr(s) and "hunter2-secret" not in repr(s)
    assert GOOD_KEY not in str(s)


@pytest.mark.parametrize("env", ["production", "staging"])
@pytest.mark.parametrize("password", [None, "", "   "])
def test_falkordb_password_required_outside_dev(env, password):
    e = {**PROD, "SEMANTICA_ENV": env}
    if password is None:
        del e["FALKORDB_PASSWORD"]
    else:
        e["FALKORDB_PASSWORD"] = password
    with pytest.raises(ConfigError, match="FALKORDB_PASSWORD"):
        Settings.from_env(e)


def test_falkordb_password_optional_in_development():
    s = Settings.from_env({"SEMANTICA_ENV": "development", "SEMANTICA_API_KEY": "k"})
    assert s.falkordb_password is None
