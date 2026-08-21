"""CLI: `pre intake` and `pre show`."""

from __future__ import annotations

import argparse
import sys

from pre.db import DEFAULT_DB_URL, init_db, make_engine, make_session_factory
from pre.intake import apply_intake_file
from pre.taxonomy import DIMENSIONS, validate
from pre.view import render_profile


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pre", description="Personal Relevance Engine")
    parser.add_argument("--db", default=DEFAULT_DB_URL, help="SQLAlchemy database URL")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_db(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", default=argparse.SUPPRESS, help="SQLAlchemy database URL")

    intake = sub.add_parser("intake", help="Build the Profile skeleton from an interview")
    add_db(intake)
    intake.add_argument("--file", help="YAML intake document (omit for interactive mode)")

    show = sub.add_parser("show", help="Render the Profile tree")
    add_db(show)
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


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "intake":
        return _cmd_intake(args)
    return _cmd_show(args)


if __name__ == "__main__":
    raise SystemExit(main())
