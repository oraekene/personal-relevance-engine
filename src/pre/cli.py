"""CLI: profile intake/viewing, watchlist, firehose ingestion, extraction queue."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session

from pre.change_corpus import ingest_entries
from pre.db import DEFAULT_DB_URL, init_db, make_engine, make_session_factory
from pre.firehose import fetch_feed, parse_feed
from pre.intake import apply_intake_file
from pre.models import Change
from pre.queue import accept, reject, render_pending
from pre.taxonomy import DIMENSIONS, validate
from pre.tranche1 import import_source_file
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
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
