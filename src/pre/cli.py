"""CLI: profile intake/viewing, watchlist, firehose ingestion, extraction queue."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from sqlalchemy.orm import Session

from pre.change_corpus import ingest_entries
from pre.cost_meter import BudgetExceeded, check_cap, render_costs
from pre.coverage import render_coverage
from pre.db import DEFAULT_DB_URL, init_db, make_engine, make_session_factory
from pre.firehose import fetch_feed, parse_feed
from pre.intake import apply_intake_file
from pre.judge import JudgeVerdict, LLMJudge
from pre.live import import_live_file
from pre.models import Change
from pre.queue import accept, reject, render_pending
from pre.retrieval import index_all, render_shortlist, shortlist_for_change
from pre.scoring import judge_change, render_scores
from pre.taxonomy import DIMENSIONS, validate
from pre.tranche1 import import_source_file
from pre.tranche2 import import_tranche2_file
from pre.tranche3 import import_tranche3_file
from pre.view import render_profile
from pre.watchlist import active_watchlist_product_names, sync_watchlist


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pre", description="Personal Relevance Engine")
    parser.add_argument("--db", default=DEFAULT_DB_URL, help="SQLAlchemy database URL")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_db(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", default=argparse.SUPPRESS, help="SQLAlchemy database URL")

    intake = sub.add_parser("intake", help="Build the Profile skeleton from an interview")
    add_db(intake)
    intake.add_argument("--file", help="YAML intake document (omit for interactive mode)")

    sub.add_parser("show", help="Render the Profile tree")

    wl = sub.add_parser("sync-watchlist", help="Sync Watchlist with Profile Tools")
    add_db(wl)

    fh = sub.add_parser("ingest-firehose", help="Pull one Firehose feed into the corpus")
    add_db(fh)
    fh.add_argument("--url", required=True, help="RSS/Atom feed URL")
    fh.add_argument("--source", required=True, help="Lane name recorded on Changes")

    sub.add_parser("changes", help="List the Change corpus")

    imp = sub.add_parser("import", help="Import one tranche-1 source file into the queue")
    add_db(imp)
    imp.add_argument("--tier", required=True, choices=["financial", "commerce", "takeout"])
    imp.add_argument("--file", required=True)

    q = sub.add_parser("queue", help="List pending extraction proposals")
    add_db(q)

    acc = sub.add_parser("accept", help="Accept a proposal by id (writes the Profile)")
    add_db(acc)
    acc.add_argument("id", type=int)

    rej = sub.add_parser("reject", help="Reject a proposal by id")
    add_db(rej)
    rej.add_argument("id", type=int)

    idx = sub.add_parser("index", help="(Re)index Profile, Network, and Change embeddings")
    add_db(idx)

    sl = sub.add_parser("shortlist", help="Inspect the candidate shortlist for one Change")
    add_db(sl)
    sl.add_argument("change_id", type=int)
    sl.add_argument("--top", type=int, default=8)

    imp2 = sub.add_parser(
        "import2", help="Import a tranche-2 source file (comms/notes/social)"
    )
    add_db(imp2)
    imp2.add_argument("--kind", required=True, choices=["comms", "notes", "social"])
    imp2.add_argument("--file", required=True)

    live = sub.add_parser(
        "import-live", help="Pull a live-connector document (calendar/email)"
    )
    add_db(live)
    live.add_argument("--kind", required=True, choices=["calendar", "email"])
    live.add_argument("--file", required=True)

    imp3 = sub.add_parser(
        "import3", help="Import a tranche-3 source file (device/health/work-systems)"
    )
    add_db(imp3)
    imp3.add_argument("--kind", required=True, choices=["device", "health", "work-systems"])
    imp3.add_argument("--file", required=True)

    judge = sub.add_parser("judge", help="Run the judge on a Change's shortlist")
    add_db(judge)
    judge.add_argument("change_id", type=int)
    judge.add_argument("--top", type=int, default=8)
    judge.add_argument(
        "--llm", action="store_true",
        help="Use the configured LLM API (default: scripted demo judge)",
    )

    costs_cmd = sub.add_parser("costs", help="LLM spend month-to-date vs cap")
    add_db(costs_cmd)

    cov = sub.add_parser("coverage", help="Profile coverage across the 17 Life Dimensions")
    add_db(cov)
    return parser


def _cmd_intake(args: argparse.Namespace) -> int:
    validate()
    engine = make_engine(args.db)
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        if args.file:
            summary = apply_intake_file(session, args.file)
        else:
            print("Interactive intake is not implemented yet; use --file with a YAML document.")
            print(f"Canonical dimensions: {', '.join(d.code for d in DIMENSIONS)}")
            return 2
        print(
            f"Intake applied: {summary.dimensions} dimensions, {summary.goals} goals, "
            f"{summary.needs} needs, {summary.activities} activities, {summary.tasks} tasks, "
            f"{summary.tools} tools, network: {summary.people} people / "
            f"{summary.organizations} organizations ({summary.total()} assertions total)."
        )
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; print any failure readably
        print(f"intake failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


def _cmd_show(args: argparse.Namespace) -> int:
    engine = make_engine(args.db)
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        print(render_profile(session))
        return 0
    finally:
        session.close()


def _open_session(db_url: str) -> Session:
    engine = make_engine(db_url)
    init_db(engine)
    return make_session_factory(engine)()


def _cmd_sync_watchlist(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        result = sync_watchlist(session)
        print(
            f"Watchlist synced: {result.added} added, {result.deactivated} deactivated. "
            f"Active: {', '.join(active_watchlist_product_names(session)) or '(none)'}"
        )
        return 0
    finally:
        session.close()


def _cmd_ingest_firehose(args: argparse.Namespace) -> int:
    xml = fetch_feed(args.url)
    entries = parse_feed(xml)
    session = _open_session(args.db)
    try:
        result = ingest_entries(session, entries, args.source)
        print(
            f"Firehose '{args.source}': {len(entries)} entries, "
            f"{result.created} new Changes, {result.deduped} deduplicated."
        )
        return 0
    finally:
        session.close()


def _cmd_changes(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    session = _open_session(args.db)
    try:
        changes = session.scalars(select(Change).order_by(Change.first_seen_at.desc())).all()
        print(f"CHANGE CORPUS ({len(changes)})")
        print("=" * 60)
        for change in changes:
            lanes = ", ".join(s["source"] for s in change.sources_json)
            flags = [f for f in ("pricing", "deprecation", "security")
                     if getattr(change, f"is_{f}")]
            flag_text = f" [{', '.join(flags)}]" if flags else ""
            print(f"  #{change.id} ({change.change_type}){flag_text} {change.product_name}")
            print(f"      {change.title}")
            print(f"      lanes: {lanes}")
        return 0
    finally:
        session.close()


def _cmd_import(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        result = import_source_file(session, args.tier, args.file)
        mode = "first connect (full history)" if result.first_connect else "delta"
        print(
            f"Imported {args.tier}:{args.file} [{mode}] — "
            f"{result.proposals_new} new proposals, "
            f"{result.proposals_strengthened} strengthened."
        )
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; print any failure readably
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


def _cmd_queue(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        print(render_pending(session))
        return 0
    finally:
        session.close()


def _cmd_accept(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        applied = accept(session, args.id)
        if applied is None:
            print(f"proposal #{args.id} not found or already decided", file=sys.stderr)
            return 1
        sync_watchlist(session)
        print(f"Accepted #{args.id}: Tool '{applied.name}' written to Profile + Watchlist.")
        return 0
    finally:
        session.close()


def _cmd_reject(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        if not reject(session, args.id):
            print(f"proposal #{args.id} not found or already decided", file=sys.stderr)
            return 1
        print(f"Rejected #{args.id}.")
        return 0
    finally:
        session.close()


def _cmd_index(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        counts = index_all(session)
        print(
            f"Indexed {counts['entities']} entities, {counts['changes']} changes "
            f"({counts['skipped']} unchanged, skipped)."
        )
        return 0
    finally:
        session.close()


def _cmd_shortlist(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:

        change = session.get(Change, args.change_id)
        if change is None:
            print(f"change #{args.change_id} not found", file=sys.stderr)
            return 1
        candidates = shortlist_for_change(session, args.change_id, top_k=args.top)
        print(render_shortlist(change, candidates))
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()


def _cmd_import2(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        result = import_tranche2_file(session, args.kind, args.file)
        print(
            f"Imported {args.kind}:{args.file} — {result['proposals_new']} new proposals, "
            f"{result['strengthened']} strengthened, "
            f"{result['skipped_already_in_profile']} skipped (already in Profile)."
        )
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; print any failure readably
        print(f"import2 failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


def _cmd_import_live(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        result = import_live_file(session, args.kind, args.file)
        print(
            f"Pulled live-{args.kind}:{args.file} — {result['proposals_new']} new proposals, "
            f"{result['strengthened']} strengthened, {result['auto_accepted']} auto-accepted "
            f"(pre-approved class)."
        )
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; print any failure readably
        print(f"import-live failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


def _cmd_import3(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        result = import_tranche3_file(session, args.kind, args.file)
        print(
            f"Imported {args.kind}:{args.file} — {result['proposals_new']} new proposals, "
            f"{result['strengthened']} strengthened."
        )
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; print any failure readably
        print(f"import3 failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


class _DemoJudge:
    """Deterministic offline judge for local runs (no API needed).

    Scores lexical overlap between the Change text and the candidate label — a
    stand-in for LLMJudge so the pipeline is runnable without credentials.
    """

    name = "demo:lexical"

    def score(self, session, change, candidates):  # type: ignore[no-untyped-def]
        from pre.embeddings import HashingEmbedder, cosine

        embedder = HashingEmbedder()
        query = embedder.embed(f"{change.product_name} {change.title}")
        verdicts = []
        for c in candidates:
            score = round(cosine(query, embedder.embed(c.label)) * 100)
            verdicts.append(
                JudgeVerdict(
                    entity_type=c.entity_type,
                    entity_id=c.entity_id,
                    score=score,
                    reasoning=(
                        f"lexical similarity between the change and this "
                        f"{c.entity_type} ('{c.label}')"
                    ),
                )
            )
        return verdicts


def _cmd_judge(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        if args.llm:
            judge: Any = LLMJudge()
        else:
            judge = _DemoJudge()
        written = judge_change(session, args.change_id, judge, top_k=args.top)
        status = check_cap(session)
        print(
            f"Judged change #{args.change_id}: {written} scores stored. "
            f"Spend MTD {status.spent_cents}/{status.cap_cents} cents "
            f"({status.pct_used:.0%})."
        )
        print(render_scores(session, args.change_id))
        return 0
    except BudgetExceeded as exc:
        print(f"budget: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 -- CLI boundary; print any failure readably
        print(f"judge failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


def _cmd_costs(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        print(render_costs(session))
        return 0
    finally:
        session.close()


def _cmd_coverage(args: argparse.Namespace) -> int:
    session = _open_session(args.db)
    try:
        print(render_coverage(session))
        return 0
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {
        "intake": _cmd_intake,
        "show": _cmd_show,
        "sync-watchlist": _cmd_sync_watchlist,
        "ingest-firehose": _cmd_ingest_firehose,
        "changes": _cmd_changes,
        "import": _cmd_import,
        "queue": _cmd_queue,
        "accept": _cmd_accept,
        "reject": _cmd_reject,
        "index": _cmd_index,
        "shortlist": _cmd_shortlist,
        "import2": _cmd_import2,
        "import-live": _cmd_import_live,
        "import3": _cmd_import3,
        "judge": _cmd_judge,
        "costs": _cmd_costs,
        "coverage": _cmd_coverage,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
