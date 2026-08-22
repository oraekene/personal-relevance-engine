"""Cost meter: every LLM call logged, month-to-date tracked, cap enforced.

Doctrine (from the user's radar system): cost is modeled, metered, and capped — the
alert fires before the invoice does. The judge refuses to run once the monthly cap is
spent (BudgetExceeded), and warns at the 80% watermark.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import LLMCallLog

DEFAULT_MONTHLY_CAP_CENTS = 2000  # $20/month; override with PRE_MONTHLY_CAP_CENTS
WARN_WATERMARK = 0.8


class BudgetExceeded(RuntimeError):
    """The monthly LLM cap is spent; judge calls are refused until next month."""


@dataclass(frozen=True)
class CallRecord:
    purpose: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd_cents: float
    change_id: int | None = None


def monthly_cap_cents() -> int:
    raw = os.environ.get("PRE_MONTHLY_CAP_CENTS", "")
    try:
        return int(raw) if raw else DEFAULT_MONTHLY_CAP_CENTS
    except ValueError:
        return DEFAULT_MONTHLY_CAP_CENTS


def log_call(session: Session, record: CallRecord) -> LLMCallLog:
    row = LLMCallLog(
        purpose=record.purpose,
        model=record.model,
        prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens,
        cost_usd_cents=record.cost_usd_cents,
        change_id=record.change_id,
    )
    session.add(row)
    session.commit()
    return row


def _month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_to_date_cents(session: Session, now: datetime | None = None) -> float:
    start = _month_start(now)
    rows = session.scalars(select(LLMCallLog).where(LLMCallLog.called_at >= start)).all()
    return sum(row.cost_usd_cents for row in rows)


@dataclass(frozen=True)
class CapStatus:
    spent_cents: float
    cap_cents: int
    pct_used: float
    exceeded: bool
    should_warn: bool


def check_cap(session: Session, now: datetime | None = None) -> CapStatus:
    cap = monthly_cap_cents()
    spent = month_to_date_cents(session, now)
    pct = spent / cap if cap > 0 else 1.0
    return CapStatus(
        spent_cents=round(spent, 2),
        cap_cents=cap,
        pct_used=round(pct, 4),
        exceeded=pct >= 1.0,
        should_warn=pct >= WARN_WATERMARK and pct < 1.0,
    )


def enforce_budget(session: Session) -> CapStatus:
    """Raise BudgetExceeded when the cap is spent; otherwise return status."""
    status = check_cap(session)
    if status.exceeded:
        raise BudgetExceeded(
            f"monthly LLM cap spent: {status.spent_cents}/{status.cap_cents} cents"
        )
    return status


def render_costs(session: Session) -> str:
    status = check_cap(session)
    by_purpose: dict[str, float] = {}
    for row in session.scalars(
        select(LLMCallLog).where(LLMCallLog.called_at >= _month_start())
    ).all():
        by_purpose[row.purpose] = by_purpose.get(row.purpose, 0.0) + row.cost_usd_cents

    lines = [
        (
            f"LLM SPEND (month to date): {status.spent_cents} / {status.cap_cents} cents "
            f"({status.pct_used:.0%})"
        ),
    ]
    if status.exceeded:
        lines.append("  !! CAP EXCEEDED — judge calls are refused until next month")
    elif status.should_warn:
        lines.append("  !! warning: >=80% of the monthly cap spent")
    for purpose, cents in sorted(by_purpose.items()):
        lines.append(f"  {purpose}: {cents:.2f} cents")
    if not by_purpose:
        lines.append("  (no calls this month)")
    return "\n".join(lines)


def estimate_cost_cents(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Rough per-model pricing table; constants here are starting parameters."""
    pricing: dict[str, tuple[float, float]] = {
        # per-million-token USD: (prompt, completion)
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "claude-sonnet": (3.00, 15.00),
    }
    prompt_usd, completion_usd = pricing.get(model, (1.00, 3.00))
    return (
        prompt_tokens / 1_000_000 * prompt_usd + completion_tokens / 1_000_000 * completion_usd
    ) * 100
