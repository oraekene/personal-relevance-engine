"""Watchlist: Tools in the Profile are monitored products, kept in sync automatically."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import Tool, WatchlistItem


@dataclass
class SyncResult:
    added: int = 0
    deactivated: int = 0


def sync_watchlist(session: Session) -> SyncResult:
    """Every Tool becomes an active Watchlist item; removed Tools deactivate theirs.

    Idempotent: safe to run after every intake/extraction/accept.
    """
    result = SyncResult()
    tool_ids = set(session.scalars(select(Tool.id)).all())
    items = session.scalars(select(WatchlistItem)).all()
    by_tool = {item.tool_id: item for item in items}

    for tool_id in tool_ids - set(by_tool):
        session.add(WatchlistItem(tool_id=tool_id))
        result.added += 1

    for tool_id, item in by_tool.items():
        if tool_id not in tool_ids and item.active:
            item.active = False
            from pre.models import utcnow  # local import to avoid cycle at module load

            item.deactivated_at = utcnow()
            result.deactivated += 1

    session.commit()
    return result


def active_watchlist_product_names(session: Session) -> list[str]:
    rows = session.execute(
        select(Tool.name).join(WatchlistItem, WatchlistItem.tool_id == Tool.id).where(
            WatchlistItem.active.is_(True)
        )
    ).all()
    return sorted(name for (name,) in rows)
