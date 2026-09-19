"""VPS-only, deterministic, notification-only Phase 4 Cron entry point."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from proactive_core.model import utc_iso
from proactive_core.phase4 import (
    PHASE4_TEST_EVENT_ID,
    Phase4Config,
    Phase4NotificationRunner,
    read_cron_delivery_observation,
    reconcile_pending_deliveries,
    synthetic_delivery_test_event,
)
from proactive_core.store import SQLiteEventStore, StoreError


PROBE_NAME = "proactive_monitor.py"
MARKER_NAME = "phase4a-test.pending"
MARKER_TOKEN = "phase4a-test:v1"
RUNTIME_DIR_NAME = "proactive-core-v1"
STATE_DIR_NAME = "state"
CONFIG_NAME = "config.json"
ERRORS_NAME = "phase4-errors.jsonl"


def _hermes_home() -> Path:
    script_dir = Path(__file__).resolve().parent
    if script_dir.name == "scripts":
        return script_dir.parent
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return script_dir


def _private_runtime_dir(path: Path) -> None:
    if path.is_symlink():
        raise StoreError("runtime directory cannot be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)
        if path.stat().st_mode & 0o077:
            raise StoreError("runtime directory permissions are not private")


def _record_error(runtime_dir: Path, code: str) -> None:
    if not code.replace("_", "").isalnum():
        code = "runtime_error"
    try:
        _private_runtime_dir(runtime_dir)
        path = runtime_dir / ERRORS_NAME
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            entry = json.dumps(
                {"occurred_at": utc_iso(), "error_code": code},
                sort_keys=True,
                separators=(",", ":"),
            ) + "\n"
            os.write(descriptor, entry.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except Exception:
        return


def _probe_snapshot(hermes_home: Path) -> dict[str, Any]:
    probe = hermes_home / "scripts" / PROBE_NAME
    if probe.is_symlink() or not probe.is_file():
        raise ValueError("probe_unavailable")
    environment = {
        "HERMES_HOME": str(hermes_home),
        "HOME": os.environ.get("HOME", "/home/mzer8"),
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C.UTF-8",
    }
    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(probe.parent),
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        raise ValueError("probe_failed")
    snapshot = json.loads(result.stdout)
    if not isinstance(snapshot, dict):
        raise ValueError("probe_malformed")
    return snapshot


def _openviking_healthy() -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", 19333, timeout=3)
    try:
        connection.request("GET", "/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        return response.status == 200
    except (http.client.HTTPException, TimeoutError, OSError):
        return False
    finally:
        connection.close()


def _consume_test_marker(marker: Path) -> bool:
    if not marker.exists():
        return False
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("test_marker_invalid")
    if marker.stat().st_size > 128:
        raise ValueError("test_marker_invalid")
    if marker.read_text(encoding="utf-8").strip() != MARKER_TOKEN:
        raise ValueError("test_marker_invalid")
    consumed = marker.with_name(marker.name + ".consumed")
    if consumed.exists() or consumed.is_symlink():
        raise ValueError("test_marker_already_consumed")
    os.replace(marker, consumed)
    if os.name == "posix":
        consumed.chmod(0o600)
    return True


def _phase4a_event(store: SQLiteEventStore):
    stored = store.get_event(PHASE4_TEST_EVENT_ID)
    return stored or synthetic_delivery_test_event(datetime.now(timezone.utc))


def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--reconcile-only", action="store_true")
    parser.add_argument("--replay-phase4a-test", action="store_true")
    args, unknown = parser.parse_known_args(argv)
    if unknown or (args.reconcile_only and args.replay_phase4a_test):
        return 0

    hermes_home = _hermes_home()
    runtime_dir = hermes_home / RUNTIME_DIR_NAME
    try:
        _private_runtime_dir(runtime_dir)
        config = Phase4Config.load(runtime_dir / CONFIG_NAME)
        switches = config.switches(os.environ)
        if switches.actions_enabled:
            _record_error(runtime_dir, "actions_switch_must_remain_false")
            return 0
        if not switches.proactive_enabled or not switches.notifications_enabled:
            return 0

        state_dir = runtime_dir / STATE_DIR_NAME
        store = SQLiteEventStore(state_dir)
        store.prune_expired(now=datetime.now(timezone.utc))
        observation = read_cron_delivery_observation(
            hermes_home,
            trusted_target_sha256=config.delivery_target_sha256,
        )
        if not observation.route_verified:
            _record_error(runtime_dir, "delivery_route_unverified")
            return 0

        reconcile_pending_deliveries(store, observation)
        if args.reconcile_only:
            return 0

        runner = Phase4NotificationRunner(
            store,
            switches,
            target_verified=observation.route_verified,
        )
        if args.replay_phase4a_test:
            event = _phase4a_event(store)
            result = runner.run_events([event], allow_test_event=True)
        else:
            marker = runtime_dir / MARKER_NAME
            if _consume_test_marker(marker):
                event = _phase4a_event(store)
                result = runner.run_events([event], allow_test_event=True)
            else:
                snapshot = _probe_snapshot(hermes_home)
                result = runner.run_snapshot(
                    snapshot,
                    openviking_healthy=_openviking_healthy(),
                )

        if result.llm_calls != 0 or result.actions_executed != 0:
            _record_error(runtime_dir, "forbidden_activity_detected")
            return 0
        if result.error_code:
            _record_error(runtime_dir, result.error_code)
            return 0
        if result.stdout_text:
            sys.stdout.write(result.stdout_text)
            sys.stdout.flush()
        return 0
    except Exception as error:
        code = getattr(error, "code", None)
        if not isinstance(code, str) or not code.replace("_", "").isalnum():
            code = "runtime_error"
        _record_error(runtime_dir, code)
        return 0


def main() -> int:
    return _run()


if __name__ == "__main__":
    raise SystemExit(main())
