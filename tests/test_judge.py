from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.cost_meter import (
    BudgetExceeded,
    CallRecord,
    check_cap,
    enforce_budget,
    log_call,
    month_to_date_cents,
)
from pre.intake import apply_intake_dict
from pre.judge import LLMJudge, ScriptedJudge, build_prompt, parse_verdicts
from pre.models import Change, ChangeScore, Tool
from pre.retrieval import ShortlistCandidate, index_all, shortlist_for_change
from pre.scoring import judge_change, scores_for_change


def _seed_apollo_scenario(session: Session) -> Change:
    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": "business",
                    "goals": [
                        {
                            "title": "Outbound prospecting machine",
                            "needs": [
                                {
                                    "title": "Predictable lead flow",
                                    "activities": [
                                        {
                                            "title": "Send outbound sequences in Apollo",
                                            "tasks": [
                                                {"title": "Pull new leads", "tools": ["Apollo"]}
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    entry = FirehoseEntry(
        product_name="Apollo",
        title="Apollo pricing update for Teams plans",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    ingest_entries(session, [entry], "fixture-lane")
    index_all(session)
    return session.query(Change).one()


def test_scripted_judge_stores_scores_and_reasoning(session: Session) -> None:
    change = _seed_apollo_scenario(session)
    tool = session.query(Tool).one()
    judge = ScriptedJudge({("tool", tool.id): (88, "user relies on Apollo daily")})

    written = judge_change(session, change.id, judge)

    assert written >= 1
    scores = scores_for_change(session, change.id)
    top = scores[0]
    assert top.entity_type == "tool"
    assert top.score == 88
    assert top.reasoning == "user relies on Apollo daily"
    assert top.judge_name == "scripted"


def test_rejudging_upserts_instead_of_duplicating(session: Session) -> None:
    change = _seed_apollo_scenario(session)
    tool = session.query(Tool).one()
    judge = ScriptedJudge({("tool", tool.id): (50, "moderate")})

    first = judge_change(session, change.id, judge)
    second = judge_change(session, change.id, judge)

    assert first >= 1 and second >= 1
    assert session.query(ChangeScore).count() == first  # upsert, not append
    assert scores_for_change(session, change.id)[0].score == 50


def test_build_prompt_includes_change_and_context() -> None:
    candidate = ShortlistCandidate(entity_type="tool", entity_id=1, label="Notion", score=0.5)
    prompt = build_prompt(
        FirehoseEntry(product_name="x", title="t").__class__ and _fake_change(),  # type: ignore[arg-type]
        [candidate],
        {("tool", 1): "Tool the user relies on: Notion"},
    )

    assert "[feature] x: t" in prompt
    assert "Tool the user relies on: Notion" in prompt
    assert '"verdicts"' in prompt


def _fake_change() -> Change:
    return Change(
        id=1,
        product_name="x",
        title="t",
        change_type="feature",
        fingerprint="f" * 64,
    )


def test_parse_verdicts_tolerates_fences_clamps_and_drops_bad_rows() -> None:
    candidates = [
        ShortlistCandidate(entity_type="tool", entity_id=1, label="A", score=0.5),
        ShortlistCandidate(entity_type="tool", entity_id=2, label="B", score=0.4),
    ]
    raw = (
        '```json\n{"verdicts": [{"index": 0, "score": 85, "reason": "hit"}, '
        '{"index": 1, "score": 250, "reason": "clamp"}, '
        '{"index": 9, "score": 10, "reason": "dropped"}]}\n```'
    )
    verdicts = parse_verdicts(raw, candidates)

    assert len(verdicts) == 2  # out-of-range index dropped
    assert verdicts[0].score == 85
    assert verdicts[1].score == 100  # clamped to the ceiling


def test_llmjudge_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRE_LLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PRE_LLM_API_KEY"):
        LLMJudge()


# --- cost meter -----------------------------------------------------------------


def test_cost_meter_sums_month_and_enforces_cap(session: Session) -> None:
    log_call(session, CallRecord("judge", "gpt-4o-mini", 1000, 500, 3.0))
    log_call(session, CallRecord("judge", "gpt-4o-mini", 2000, 1000, 6.0))

    assert month_to_date_cents(session) == pytest.approx(9.0)
    status = check_cap(session)
    assert status.exceeded is False
    assert status.should_warn is False
    enforce_budget(session)  # does not raise


def test_cost_meter_warns_at_watermark_and_blocks_at_cap(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    monkeypatch.setenv("PRE_MONTHLY_CAP_CENTS", "500")
    log_call(session, CallRecord("judge", "gpt-4o", 100000, 50000, 420.0))
    warned = check_cap(session)
    assert warned.should_warn is True and warned.exceeded is False
    enforce_budget(session)  # still allowed at 84%

    log_call(session, CallRecord("judge", "gpt-4o", 100000, 50000, 90.0))
    over = check_cap(session)
    assert over.exceeded is True
    with pytest.raises(BudgetExceeded):
        enforce_budget(session)


def test_shortlist_feeds_judge_seam_end_to_end(session: Session) -> None:
    change = _seed_apollo_scenario(session)
    candidates = shortlist_for_change(session, change.id, top_k=3)
    judge = ScriptedJudge({})
    verdicts = judge.score(session, change, candidates)

    assert len(verdicts) == len(candidates)
    assert all(v.score == 0 for v in verdicts)  # unscripted pairs default to no-opinion
