"""The production entrypoint (`python -m app`) as a real process.

Railway's deploy healthcheck connects over IPv4. These tests start the actual
server process and connect to it the same way, so a listener that is not
reachable over IPv4 fails here rather than on Railway.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from app.__main__ import dual_stack_socket
from app.config import ConfigError, Settings

SERVICE_DIR = Path(__file__).resolve().parent.parent
BASE_ENV = {
    "SEMANTICA_ENV": "development",
    "SEMANTICA_API_KEY": "k",
    "FALKORDB_HOST": "127.0.0.1",
    "FALKORDB_PORT": "1",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ipv6_available() -> bool:
    try:
        dual_stack_socket(0).close()
        return True
    except OSError:
        return False


def _start(extra_env: dict[str, str]) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    env = {**os.environ, **BASE_ENV, "PORT": str(port), **extra_env}
    proc = subprocess.Popen(
        [sys.executable, "-m", "app"],
        cwd=SERVICE_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc, port


def _wait_health(url: str, proc: subprocess.Popen, timeout: float = 15.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            return urllib.request.urlopen(url, timeout=1).status
        except OSError:
            time.sleep(0.2)
    return -1


def _stop(proc: subprocess.Popen) -> str:
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return out


def _bind_event(output: str) -> dict:
    for line in output.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("event") == "bind":
            return entry
    raise AssertionError(f"no bind event in output:\n{output}")


def test_default_bind_is_ipv4_all_interfaces():
    assert Settings.from_env(BASE_ENV).bind_host == "0.0.0.0"  # noqa: S104


@pytest.mark.parametrize("value", ["127.0.0.1", "localhost", "*", "[::]", "0.0.0.0 "])
def test_bind_host_override_is_validated(value):
    env = {**BASE_ENV, "SEMANTICA_BIND_HOST": value}
    if value.strip() in ("0.0.0.0", "::"):
        assert Settings.from_env(env).bind_host == value.strip()
    else:
        with pytest.raises(ConfigError, match="SEMANTICA_BIND_HOST"):
            Settings.from_env(env)


def test_default_process_is_reachable_over_ipv4():
    """Regression for the Railway outage: the served socket must answer on
    IPv4, which is how Railway's healthcheck connects."""
    proc, port = _start({})
    status = _wait_health(f"http://127.0.0.1:{port}/health", proc)
    out = _stop(proc)
    assert status == 200, out
    bind = _bind_event(out)
    assert bind["host"] == "0.0.0.0" and bind["port"] == port


@pytest.mark.skipif(not _ipv6_available(), reason="no IPv6 in this runtime")
def test_dual_stack_override_serves_ipv4_and_ipv6():
    proc, port = _start({"SEMANTICA_BIND_HOST": "::"})
    v4 = _wait_health(f"http://127.0.0.1:{port}/health", proc)
    v6 = _wait_health(f"http://[::1]:{port}/health", proc)
    out = _stop(proc)
    assert (v4, v6) == (200, 200), out
    bind = _bind_event(out)
    assert bind["host"] == "::" and bind["ipv6_only"] is False


@pytest.mark.skipif(_ipv6_available(), reason="runtime has IPv6")
def test_dual_stack_override_fails_loudly_without_ipv6():
    """Where IPv6 is unavailable the override must not start a server that
    looks healthy but is unreachable: it exits 2 with a bind_failed event."""
    proc, _ = _start({"SEMANTICA_BIND_HOST": "::"})
    out, _ = proc.communicate(timeout=15)
    assert proc.returncode == 2, out
    assert '"bind_failed"' in out


def test_dual_stack_socket_sets_v6only_off():
    if not _ipv6_available():
        with pytest.raises(OSError):
            dual_stack_socket(0)
        return
    sock = dual_stack_socket(0)
    try:
        assert sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 0
    finally:
        sock.close()
