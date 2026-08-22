"""The judge: stage 3 of the matching funnel (ADR-0002).

`Judge` is the seam (per spec Testing Decisions): tests substitute a `ScriptedJudge`.
`LLMJudge` calls an OpenAI-compatible chat-completions endpoint configured via env
(PRE_LLM_API_KEY, PRE_LLM_BASE_URL, PRE_LLM_MODEL) — never in tests. Every call is
cost-metered; the monthly cap is enforced before the request is sent.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from pre.cost_meter import (
    CallRecord,
    enforce_budget,
    estimate_cost_cents,
    log_call,
)
from pre.models import (
    Activity,
    Change,
    Goal,
    Need,
    Organization,
    Person,
    Task,
    Tool,
)
from pre.retrieval import ShortlistCandidate


@dataclass(frozen=True)
class JudgeVerdict:
    entity_type: str
    entity_id: int
    score: int  # 0-100
    reasoning: str
    call_id: int | None = None


class Judge(Protocol):
    name: str

    def score(
        self, session: Session, change: Change, candidates: list[ShortlistCandidate]
    ) -> list[JudgeVerdict]: ...


def _entity_context(session: Session, candidate: ShortlistCandidate) -> str:
    """Full Profile context for one candidate entity (decision 8: rich context)."""
    kind, eid = candidate.entity_type, candidate.entity_id
    row: Any
    if kind == "goal":
        row = session.get(Goal, eid)
        if row:
            return f"Goal: {row.title} (horizon: {row.horizon or 'unspecified'})"
    elif kind == "need":
        row = session.get(Need, eid)
        if row:
            return (
                f"Need: {row.title} (horizon: {row.horizon or '?'}, "
                f"pain: {row.pain_level}/10, openness: {row.openness_to_change or '?'})"
            )
    elif kind == "activity":
        row = session.get(Activity, eid)
        if row:
            return f"Activity: {row.title} (cadence: {row.cadence or '?'})"
    elif kind == "task":
        row = session.get(Task, eid)
        if row:
            return f"Task: {row.title}"
    elif kind == "tool":
        row = session.get(Tool, eid)
        if row:
            return f"Tool the user relies on: {row.name}"
    elif kind == "person":
        row = session.get(Person, eid)
        if row:
            return f"Person in the user's network: {row.display_name}"
    elif kind == "organization":
        row = session.get(Organization, eid)
        if row:
            return f"Organization in the user's network: {row.name}"
    return f"{kind}:{eid}"


def build_prompt(change: Change, candidates: list[ShortlistCandidate], contexts: dict[tuple[str, int], str]) -> str:
    """Pure function so prompt construction is testable without any API."""
    lines = [
        "You judge whether a product change matters to one specific person.",
        "Score each candidate 0-100 for relevance to this person's life and work.",
        "0-20 irrelevant, 21-50 tangential, 51-75 useful, 76-100 immediately actionable.",
        "Give a one-sentence reason grounded in the person's context.",
        "",
        f"CHANGE: [{change.change_type}] {change.product_name}: {change.title}",
        "",
        "CANDIDATES:",
    ]
    for i, candidate in enumerate(candidates):
        context = contexts[(candidate.entity_type, candidate.entity_id)]
        lines.append(f"{i}. [{candidate.entity_type}] {context}")
    lines += [
        "",
        'Reply with JSON only: {"verdicts": [{"index": 0, "score": 85, "reason": "..."}]}',
    ]
    return "\n".join(lines)


def parse_verdicts(raw: str, candidates: list[ShortlistCandidate]) -> list[JudgeVerdict]:
    """Parse the model's JSON reply into verdicts; tolerate code fences and bad rows."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json")
    payload: dict[str, Any] = json.loads(text)
    verdicts: list[JudgeVerdict] = []
    for item in payload.get("verdicts", []):
        index = int(item.get("index", -1))
        if not 0 <= index < len(candidates):
            continue
        score = max(0, min(100, int(item.get("score", 0))))
        candidate = candidates[index]
        verdicts.append(
            JudgeVerdict(
                entity_type=candidate.entity_type,
                entity_id=candidate.entity_id,
                score=score,
                reasoning=str(item.get("reason", ""))[:1024],
            )
        )
    return verdicts


class ScriptedJudge:
    """Test double: returns predetermined scores by entity key."""

    def __init__(self, scores: dict[tuple[str, int], tuple[int, str]]) -> None:
        self.scores = scores
        self.name = "scripted"

    def score(
        self, session: Session, change: Change, candidates: list[ShortlistCandidate]
    ) -> list[JudgeVerdict]:
        return [
            JudgeVerdict(
                entity_type=c.entity_type,
                entity_id=c.entity_id,
                score=self.scores.get((c.entity_type, c.entity_id), (0, "no opinion"))[0],
                reasoning=self.scores.get((c.entity_type, c.entity_id), (0, ""))[1],
            )
            for c in candidates
        ]


class LLMJudge:
    """OpenAI-compatible chat-completions judge. Requires PRE_LLM_API_KEY."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model or os.environ.get("PRE_LLM_MODEL", "gpt-4o-mini")
        self.base_url = base_url or os.environ.get(
            "PRE_LLM_BASE_URL", "https://api.openai.com/v1"
        )
        self.api_key = api_key or os.environ.get("PRE_LLM_API_KEY", "")
        self.name = f"llm:{self.model}"
        if not self.api_key:
            raise RuntimeError(
                "PRE_LLM_API_KEY is not set; configure an LLM provider to use LLMJudge"
            )

    def _complete(self, prompt: str) -> tuple[str, int, int]:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage", {})
        return content, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))

    def score(
        self, session: Session, change: Change, candidates: list[ShortlistCandidate]
    ) -> list[JudgeVerdict]:
        enforce_budget(session)  # cap checked BEFORE the request goes out

        contexts = {
            (c.entity_type, c.entity_id): _entity_context(session, c) for c in candidates
        }
        prompt = build_prompt(change, candidates, contexts)
        raw, prompt_tokens, completion_tokens = self._complete(prompt)

        cost = estimate_cost_cents(self.model, prompt_tokens, completion_tokens)
        record = log_call(
            session,
            CallRecord(
                purpose="judge",
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd_cents=round(cost, 4),
                change_id=change.id,
            ),
        )
        return [
            JudgeVerdict(
                entity_type=v.entity_type,
                entity_id=v.entity_id,
                score=v.score,
                reasoning=v.reasoning,
                call_id=record.id,
            )
            for v in parse_verdicts(raw, candidates)
        ]
