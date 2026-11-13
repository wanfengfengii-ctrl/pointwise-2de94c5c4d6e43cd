# Upstream source and provenance

This is a review workspace for the unmodified pandas v2.2.2 production implementation.
Pandas repository: https://github.com/pandas-dev/pandas/tree/v2.2.2
SQLAlchemy repository: https://github.com/sqlalchemy/sqlalchemy/tree/rel_2_0_35

The pinned distribution installs the actual complete Python source modules under
site-packages; probe.py calls that implementation, not a local reimplementation.
source_manifest.py verifies the core versions and SHA-256 of all five relevant
source modules. It reports their physical paths and original symbol line numbers.
Use module paths and original line numbers in the review, not environment-specific
absolute installation paths.

Read the full original source without network access after preparation:
    python3 bootstrap.py source_manifest.py
    python3 bootstrap.py source_manifest.py --module pandas.io.sql
    python3 bootstrap.py source_manifest.py --module pandas.core.generic
    python3 bootstrap.py source_manifest.py --module sqlalchemy.engine.base
    python3 bootstrap.py source_manifest.py --module sqlalchemy.engine.default
    python3 bootstrap.py source_manifest.py --module sqlalchemy.dialects.sqlite.pysqlite

The original test_transaction_rollback is in pandas/tests/io/test_sql.py,
lines 3155-3193 at the tag above. upstream_regression.py specializes that test's
SQLAlchemy SQLite branch without requiring pytest's other database fixtures.
The original rollback/commit assertions are retained.

Review artifacts belong in this workspace. Do not patch installed production
modules, the observer, samples, source hashes, or existing regression assertions.
The local docs/to_sql-transactions.md is a downstream maintenance note to correct,
not a verbatim upstream document. Keep actual current behavior distinct from a
proposed stronger guarantee; do not present a downstream claim as an upstream promise.
