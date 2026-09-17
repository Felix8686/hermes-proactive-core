"""Optional, non-authoritative LLM enrichment; no provider or network adapter."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .privacy import sanitize_text, sanitize_value

if TYPE_CHECKING:
    from .store import SQLiteEventStore


@dataclass(frozen=True, slots=True)
class LLMEnrichment:
    next_action_candidate: str | None = None
    semantic_rank: float | None = None
    short_summary: str | None = None
    error_code: str | None = None
    calls: int = 0


class OptionalLLMEnricher:
    """Accepts a test/integration callable, but never affects detection or policy."""

    _ALLOWED_FIELDS = {"next_action_candidate", "semantic_rank", "short_summary"}

    def __init__(self, call: Callable[[dict[str, Any]], str] | None = None) -> None:
        self._call = call

    def enrich(
        self,
        context: Mapping[str, Any],
        *,
        enabled: bool = False,
        health_path: bool = True,
    ) -> LLMEnrichment:
        if not enabled or health_path or self._call is None:
            return LLMEnrichment()
        safe_context = sanitize_value(context)
        if not isinstance(safe_context, dict):
            return LLMEnrichment(error_code="llm_invalid_input", calls=0)
        try:
            raw = self._call(safe_context)
        except TimeoutError:
            return LLMEnrichment(error_code="llm_timeout", calls=1)
        except Exception:
            return LLMEnrichment(error_code="llm_unavailable", calls=1)
        if not isinstance(raw, str) or len(raw) > 4096:
            return LLMEnrichment(error_code="llm_invalid_json", calls=1)
        try:
            parsed = json.loads(raw, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        except (json.JSONDecodeError, ValueError, TypeError):
            return LLMEnrichment(error_code="llm_invalid_json", calls=1)
        if not isinstance(parsed, dict) or set(parsed) - self._ALLOWED_FIELDS:
            return LLMEnrichment(error_code="llm_invalid_json", calls=1)

        candidate = parsed.get("next_action_candidate")
        if candidate is not None:
            if not isinstance(candidate, str) or not candidate.strip() or len(candidate) > 256:
                return LLMEnrichment(error_code="llm_invalid_json", calls=1)
            candidate = sanitize_text(candidate, limit=160)
        summary = parsed.get("short_summary")
        if summary is not None:
            if not isinstance(summary, str) or len(summary) > 512:
                return LLMEnrichment(error_code="llm_invalid_json", calls=1)
            summary = sanitize_text(summary, limit=160)
        rank = parsed.get("semantic_rank")
        if rank is not None:
            if isinstance(rank, bool) or not isinstance(rank, (int, float)):
                return LLMEnrichment(error_code="llm_invalid_json", calls=1)
            rank = float(rank)
            if not math.isfinite(rank) or not 0.0 <= rank <= 1.0:
                return LLMEnrichment(error_code="llm_invalid_json", calls=1)
        return LLMEnrichment(
            next_action_candidate=candidate,
            semantic_rank=rank,
            short_summary=summary,
            calls=1,
        )

    def enrich_and_log(
        self,
        context: Mapping[str, Any],
        *,
        store: SQLiteEventStore,
        event_id: str,
        stage: str,
        enabled: bool = False,
        health_path: bool = True,
        occurred_at: str | datetime | None = None,
    ) -> LLMEnrichment:
        result = self.enrich(context, enabled=enabled, health_path=health_path)
        if result.error_code is not None or result.calls:
            store.append_llm_outcome(
                event_id,
                stage,
                result.error_code or "completed",
                calls=result.calls,
                occurred_at=occurred_at,
            )
        return result
