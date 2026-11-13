"""Observe the public to_sql call without patching pandas or SQLAlchemy."""
import argparse
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import platform
import sqlite3
import uuid

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.pool import NullPool
from samples import CASES

def snapshot(uri):
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        objects = [list(row) for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
        columns = [list(row) for row in conn.execute("PRAGMA table_info(readings)")]
        rows = [list(row) for row in conn.execute("SELECT * FROM readings ORDER BY value")]
    return {"objects": objects, "columns": columns, "rows": rows}

def validate(spec):
    required = {"if_exists", "owner", "catch", "begin", "values", "chunksize"}
    if set(spec) != required:
        raise ValueError("case fields must be " + ", ".join(sorted(required)))
    if spec["if_exists"] not in ("append", "replace"):
        raise ValueError("if_exists must be append or replace")
    if spec["owner"] not in ("engine", "connection"):
        raise ValueError("owner must be engine or connection")
    if type(spec["catch"]) is not bool or (spec["owner"] == "engine" and spec["catch"]):
        raise ValueError("catch applies only to a connection owned by the caller")
    if spec["begin"] not in ("legacy", "explicit"):
        raise ValueError("begin must be legacy or explicit")
    if type(spec["chunksize"]) is not int or spec["chunksize"] <= 0:
        raise ValueError("chunksize must be a positive integer")
    if not isinstance(spec["values"], list) or not spec["values"]:
        raise ValueError("values must be a nonempty integer list")
    if any(type(value) is not int for value in spec["values"]):
        raise ValueError("values must contain integers")

def run_case(spec):
    validate(spec)
    spec = deepcopy(spec)
    uri = "file:review_" + uuid.uuid4().hex + "?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True)
    anchor.execute("CREATE TABLE readings (value INTEGER NOT NULL CHECK(value>=0), "
                   "legacy TEXT NOT NULL DEFAULT 'old')")
    anchor.execute("CREATE INDEX readings_value ON readings(value)")
    anchor.execute("INSERT INTO readings(value) VALUES (90)")
    anchor.commit()
    trace = []
    errors = []

    def connect():
        raw = sqlite3.connect(uri, uri=True)
        # Make legacy sqlite3 transaction behavior explicit across supported Python versions.
        raw.isolation_level = ""
        if hasattr(raw, "autocommit"):
            raw.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
        raw.set_trace_callback(lambda sql: trace.append({
            "layer": "sqlite", "sql": sql, "active_before": raw.in_transaction
        }))
        return raw

    engine = sa.create_engine("sqlite://", creator=connect, poolclass=NullPool)
    if spec["begin"] == "explicit":
        @sa.event.listens_for(engine, "connect")
        def explicit_connect(raw, record):
            raw.isolation_level = None

        @sa.event.listens_for(engine, "begin")
        def explicit_begin(conn):
            conn.exec_driver_sql("BEGIN")

    def observe_event(name):
        def listener(conn):
            trace.append({
                "layer": "sqlalchemy", "event": name,
                "sa_active": conn.in_transaction(),
                "sqlite_active": conn.connection.driver_connection.in_transaction
            })
        return listener

    for name in ("begin", "commit", "rollback"):
        sa.event.listen(engine, name, observe_event(name))

    def note_error(exc, where):
        errors.append({
            "where": where, "type": type(exc).__name__, "message": str(exc),
            "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None
        })

    def write(con):
        frame = pd.DataFrame({"value": pd.Series(spec["values"], dtype=object)})
        trace.append({"layer": "caller", "event": "to_sql_enter"})
        try:
            result = frame.to_sql("readings", con, if_exists=spec["if_exists"],
                                  index=False, chunksize=spec["chunksize"],
                                  dtype={"value": sa.BigInteger()})
        except Exception:
            trace.append({"layer": "caller", "event": "to_sql_raise"})
            raise
        trace.append({"layer": "caller", "event": "to_sql_return", "value": result})
        return result

    try:
        before = snapshot(uri)
        returned = None
        try:
            if spec["owner"] == "engine":
                returned = write(engine)
            else:
                with engine.begin() as conn:
                    if spec["catch"]:
                        try:
                            returned = write(conn)
                        except Exception as exc:
                            note_error(exc, "inside_outer_transaction")
                        trace.append({"layer": "caller", "event": "outer_scope_continues"})
                    else:
                        returned = write(conn)
        except Exception as exc:
            note_error(exc, "outside_transaction_scope")
        # NullPool and disposal ensure this is not the to_sql DBAPI connection.
        engine.dispose()
        after = snapshot(uri)
        return {
            "spec": spec,
            "versions": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                         "pandas": pd.__version__, "sqlalchemy": sa.__version__},
            "returned": returned, "errors": errors, "trace": trace,
            "before": before, "after": after
        }
    finally:
        engine.dispose()
        anchor.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", nargs="?", choices=sorted(CASES))
    parser.add_argument("--spec", type=Path)
    args = parser.parse_args()
    if (args.scenario is None) == (args.spec is None):
        parser.error("provide one scenario name or --spec FILE")
    spec = json.loads(args.spec.read_text(encoding="utf-8")) if args.spec else CASES[args.scenario]
    print(json.dumps(run_case(spec), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
