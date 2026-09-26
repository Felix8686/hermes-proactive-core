"""Explicit, local fixture-only Phase 2 entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from proactive_core.model import ContractError, Event
from proactive_core.shadow import ShadowDetector
from proactive_core.store import SQLiteEventStore, StoreError
from proactive_core.switches import KillSwitches


ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = (ROOT / "fixtures").resolve()


def load_fixture(path: Path) -> list[Event]:
    resolved = path.expanduser().resolve()
    if resolved.parent != FIXTURE_ROOT or resolved.suffix.casefold() != ".json":
        raise ValueError("fixture must be a JSON file directly inside the fixtures directory")
    raw: Any = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"events"} or not isinstance(raw["events"], list):
        raise ValueError("fixture must contain only an events array")
    return [Event.from_fixture(item) for item in raw["events"]]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local Proactive Core Shadow on JSON fixtures only.")
    parser.add_argument("--enable-shadow", action="store_true")
    parser.add_argument("--fixture", type=Path, default=FIXTURE_ROOT / "health_empty.json")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".shadow-state")
    args = parser.parse_args()

    if not args.enable_shadow:
        return 0
    try:
        switches = KillSwitches.from_env()
    except ValueError:
        print("invalid_kill_switch", file=sys.stderr)
        return 2
    if not switches.proactive_enabled:
        return 0
    try:
        events = load_fixture(args.fixture)
        state_dir = args.state_dir.expanduser().resolve()
        try:
            state_dir.relative_to(ROOT)
        except ValueError as error:
            raise ValueError("state directory must remain inside this isolated project directory") from error
        store = SQLiteEventStore(state_dir)
        result = ShadowDetector(store, switches).run(events)
    except (OSError, ValueError, StoreError):
        print("shadow_fixture_or_store_error", file=sys.stderr)
        return 2
    if result.error_code:
        print(result.error_code, file=sys.stderr)
        return 1
    if result.stdout_text:
        print(result.stdout_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
