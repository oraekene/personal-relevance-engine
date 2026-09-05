# 15 — Postgres backup/restore path

**What to build:** `backup_database`/`restore_database` (`src/pre/ops.py`) support Postgres URLs via pg_dump/pg_restore instead of raising ValueError. The CLI (`pre backup --file`, `pre restore --file`) is unchanged; the subprocess runner is an injectable dependency so tests run offline with a fake runner asserting argv and file handling, mirroring the judge seam (ScriptedJudge). The SQLite file-copy path keeps its tested roundtrip.

**Blocked by:** 14 — Ops baseline (backup entry points and `LAST BACKUP` stamp exist).

**Status:** resolved

- [x] `backup_database("postgresql://...")` shells to pg_dump through an injectable runner (no subprocess in tests)
- [x] `restore_database("postgresql://...")` shells to pg_restore through the same runner
- [x] SQLite file-copy behavior unchanged with its roundtrip test intact
- [x] `pre backup`/`pre restore` work against both URL kinds; `:memory:` still refused
- [x] Module docstring no longer scopes backups to SQLite-file deployments

## Comments

- Implemented in commit a0d7fd5.
- `CommandRunner` protocol (`argv -> None`) with a subprocess default; pg_dump uses `-F c -f <dest>`, pg_restore `--clean --if-exists -d <url> <src>`; fake-runner tests lock both argvs plus the missing-dump refusal. CLI untouched (same flags, same cron contract).
- Verification: 3 new tests in tests/test_ops.py; full suite 164 passing, mypy strict, ruff clean.
