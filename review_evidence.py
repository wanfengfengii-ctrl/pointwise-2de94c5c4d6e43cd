"""Re-runnable evidence for the to_sql transaction review.

Commands:
    python3 bootstrap.py review_evidence.py run      # probe.run_case for every case -> evidence.json
    python3 bootstrap.py review_evidence.py verify   # re-run, compare to saved evidence and REVIEW.md
    python3 bootstrap.py review_evidence.py demo     # show real structure/data differences

Expectations in EXPECTATIONS are derived independently from the transaction
ownership rules and the SQLite legacy/explicit BEGIN boundary; they are never
copied from an observed probe result.  Confirmed defects are expected to keep
reproducing: verify asserts the documented mismatch, not its disappearance.
"""
import argparse
import json
from pathlib import Path

from probe import run_case

ROOT = Path(__file__).resolve().parent
EVIDENCE_PATH = ROOT / "evidence.json"

# Each case changes at most one factor relative to a neighbour.  Business
# review keeps chunksize=2 per CONTRACT.md.
CASES = [
    {"id": "E01-normal-append-engine-legacy",
     "spec": {"if_exists": "append", "owner": "engine", "catch": False,
              "begin": "legacy", "values": [1, 2, 3, 4], "chunksize": 2}},
    {"id": "E02-incident-append-engine-legacy-check",
     "spec": {"if_exists": "append", "owner": "engine", "catch": False,
              "begin": "legacy", "values": [1, 2, -3, 4], "chunksize": 2}},
    {"id": "E03-append-engine-legacy-overflow",
     "spec": {"if_exists": "append", "owner": "engine", "catch": False,
              "begin": "legacy", "values": [1, 2, 9223372036854775808, 4], "chunksize": 2}},
    {"id": "E04-append-engine-explicit-check",
     "spec": {"if_exists": "append", "owner": "engine", "catch": False,
              "begin": "explicit", "values": [1, 2, -3, 4], "chunksize": 2}},
    {"id": "E05-append-conn-legacy-check-escape",
     "spec": {"if_exists": "append", "owner": "connection", "catch": False,
              "begin": "legacy", "values": [1, 2, -3, 4], "chunksize": 2}},
    {"id": "E06-append-conn-legacy-check-caught",
     "spec": {"if_exists": "append", "owner": "connection", "catch": True,
              "begin": "legacy", "values": [1, 2, -3, 4], "chunksize": 2}},
    {"id": "E07-append-conn-explicit-check-escape",
     "spec": {"if_exists": "append", "owner": "connection", "catch": False,
              "begin": "explicit", "values": [1, 2, -3, 4], "chunksize": 2}},
    {"id": "E08-append-conn-explicit-check-caught",
     "spec": {"if_exists": "append", "owner": "connection", "catch": True,
              "begin": "explicit", "values": [1, 2, -3, 4], "chunksize": 2}},
    {"id": "E09-append-conn-legacy-success",
     "spec": {"if_exists": "append", "owner": "connection", "catch": False,
              "begin": "legacy", "values": [1, 2, 3, 4], "chunksize": 2}},
    {"id": "E10-append-conn-explicit-success",
     "spec": {"if_exists": "append", "owner": "connection", "catch": False,
              "begin": "explicit", "values": [1, 2, 3, 4], "chunksize": 2}},
    {"id": "E11-replace-engine-legacy-overflow",
     "spec": {"if_exists": "replace", "owner": "engine", "catch": False,
              "begin": "legacy", "values": [1, 2, 9223372036854775808, 4], "chunksize": 2}},
    {"id": "E12-replace-engine-explicit-overflow",
     "spec": {"if_exists": "replace", "owner": "engine", "catch": False,
              "begin": "explicit", "values": [1, 2, 9223372036854775808, 4], "chunksize": 2}},
    {"id": "E13-replace-conn-legacy-overflow-escape",
     "spec": {"if_exists": "replace", "owner": "connection", "catch": False,
              "begin": "legacy", "values": [1, 2, 9223372036854775808, 4], "chunksize": 2}},
    {"id": "E14-replace-conn-legacy-overflow-caught",
     "spec": {"if_exists": "replace", "owner": "connection", "catch": True,
              "begin": "legacy", "values": [1, 2, 9223372036854775808, 4], "chunksize": 2}},
    {"id": "E15-replace-conn-explicit-overflow-escape",
     "spec": {"if_exists": "replace", "owner": "connection", "catch": False,
              "begin": "explicit", "values": [1, 2, 9223372036854775808, 4], "chunksize": 2}},
    {"id": "E16-replace-conn-explicit-overflow-caught",
     "spec": {"if_exists": "replace", "owner": "connection", "catch": True,
              "begin": "explicit", "values": [1, 2, 9223372036854775808, 4], "chunksize": 2}},
    {"id": "E17-replace-conn-legacy-check-escape",
     "spec": {"if_exists": "replace", "owner": "connection", "catch": False,
              "begin": "legacy", "values": [1, 2, -3, 4], "chunksize": 2}},
    {"id": "E18-replace-conn-legacy-success",
     "spec": {"if_exists": "replace", "owner": "connection", "catch": False,
              "begin": "legacy", "values": [1, 2, 3, 4], "chunksize": 2}},
]

# Original table created independently in every probe (CONTRACT.md):
#   value INTEGER NOT NULL CHECK(value>=0), legacy TEXT NOT NULL DEFAULT 'old',
#   index readings_value, seed row (90,'old').
ORIGINAL_OBJECTS = [
    ["index", "readings_value", "readings",
     "CREATE INDEX readings_value ON readings(value)"],
    ["table", "readings", "readings",
     "CREATE TABLE readings (value INTEGER NOT NULL CHECK(value>=0), "
     "legacy TEXT NOT NULL DEFAULT 'old')"],
]
ORIGINAL_COLUMNS = [
    [0, "value", "INTEGER", 1, None, 0],
    [1, "legacy", "TEXT", 1, "'old'", 0],
]
REPLACED_COLUMNS = [[0, "value", "BIGINT", 0, None, 0]]
REPLACED_OBJECT = ["table", "readings", "readings",
                   "CREATE TABLE readings (\n\tvalue BIGINT\n)"]
SEED = [[90, "old"]]
# to_sql builds tables from the single-column frame with explicit BigInteger.
REPLACED_SQL_PREFIX = "CREATE TABLE readings ("

# Independent expectation records.  `physical_ddl_protected` states whether
# DROP/CREATE run inside a physical SQLite transaction given the begin mode.
# `defect` references the confirmed implementation defect class in REVIEW.md.
EXPECTATIONS = {
    "E01": {
        "kind": "success_control",
        "errors": [], "returned": 4,
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": [[1, "old"], [2, "old"], [3, "old"], [4, "old"], [90, "old"]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "why": "Engine owner: pandas opens and closes the only transaction; "
               "all four inserts commit once on success."},
    "E02": {
        "kind": "defect", "defect": "D1",
        "errors": [["outside_transaction_scope", "IntegrityError"]],
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": [[1, "old"], [2, "old"], [90, "old"]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "why": "Engine owner + legacy driver: first chunk is inside the sqlite3 "
               "implicit transaction. On the CHECK failure pandasSQL_builder's "
               "ExitStack still closes con.begin() with no exception info and "
               "COMMITs; chunk 1 survives. Structure is append-only so the "
               "table definition is untouched."},
    "E03": {
        "kind": "defect", "defect": "D1",
        "errors": [["outside_transaction_scope", "OverflowError"]],
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": [[1, "old"], [2, "old"], [90, "old"]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "why": "Same owner/begin mode as E02; OverflowError is raised while "
               "binding chunk 2 before any of its SQL is sent, but the "
               "already-open transaction is still committed on cleanup."},
    "E04": {
        "kind": "defect", "defect": "D1",
        "errors": [["outside_transaction_scope", "IntegrityError"]],
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": [[1, "old"], [2, "old"], [90, "old"]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "why": "Single-factor change vs E02 (legacy->explicit BEGIN). Physical "
               "BEGIN now exists from the start, which makes rollback "
               "possible, but pandas still exits its own context without the "
               "exception and commits; the driver recipe cannot fix D1."},
    "E05": {
        "kind": "correct_rollback_control",
        "errors": [["outside_transaction_scope", "IntegrityError"]],
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": SEED,
        "driver_calls": {"BEGIN": 1, "COMMIT": 0, "ROLLBACK": 1},
        "why": "Caller owns engine.begin(); pandas run_transaction() joins the "
               "existing transaction instead of creating one. The exception "
               "escapes, so the outer RootTransaction __exit__ rolls back."},
    "E06": {
        "kind": "caller_responsibility",
        "errors": [["inside_outer_transaction", "IntegrityError"]],
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": [[1, "old"], [2, "old"], [90, "old"]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "why": "The exception is caught inside the outer scope, which then "
               "exits normally. engine.begin() therefore commits everything "
               "in its transaction, including chunk 1. Pandas neither "
               "committed nor rolled back here; the caller's scope owns it."},
    "E07": {
        "kind": "correct_rollback_control",
        "errors": [["outside_transaction_scope", "IntegrityError"]],
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": SEED,
        "driver_calls": {"BEGIN": 1, "COMMIT": 0, "ROLLBACK": 1},
        "why": "Explicit-BEGIN twin of E05; physical transaction was opened by "
               "the begin event and the outer ROLLBACK removes chunk 1."},
    "E08": {
        "kind": "caller_responsibility",
        "errors": [["inside_outer_transaction", "IntegrityError"]],
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": [[1, "old"], [2, "old"], [90, "old"]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "why": "Explicit-BEGIN twin of E06; catching and completing the outer "
               "scope commits chunk 1 through the caller's transaction."},
    "E09": {
        "kind": "success_control",
        "errors": [], "returned": 4,
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": [[1, "old"], [2, "old"], [3, "old"], [4, "old"], [90, "old"]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "why": "Connection-owner success under legacy control; one outer commit."},
    "E10": {
        "kind": "success_control",
        "errors": [], "returned": 4,
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": [[1, "old"], [2, "old"], [3, "old"], [4, "old"], [90, "old"]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "why": "Connection-owner success under explicit BEGIN; same outcome."},
    "E11": {
        "kind": "defect", "defect": "D2", "mode": "replace",
        "errors": [["outside_transaction_scope", "OverflowError"]],
        "objects": [REPLACED_OBJECT], "columns": REPLACED_COLUMNS,
        "rows": [[1], [2]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "physical_ddl_protected": False,
        "why": "Legacy driver auto-commits DDL outside any physical "
               "transaction: DROP TABLE and CREATE TABLE are permanent before "
               "the insert transaction opens. Old columns, the CHECK "
               "constraint, the default, the index and row 90 cannot return. "
               "D1 then commits chunk 1 of the new table."},
    "E12": {
        "kind": "defect", "defect": "D1", "mode": "replace",
        "errors": [["outside_transaction_scope", "OverflowError"]],
        "objects": [REPLACED_OBJECT], "columns": REPLACED_COLUMNS,
        "rows": [[1], [2]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "physical_ddl_protected": True,
        "why": "Explicit BEGIN puts DROP/CREATE inside the physical "
               "transaction, so a true rollback could restore the old table. "
               "D1 defeats it: cleanup commits the whole unit, leaving the "
               "replaced table plus chunk 1."},
    "E13": {
        "kind": "ddl_boundary",
        "errors": [["outside_transaction_scope", "OverflowError"]],
        "objects": [REPLACED_OBJECT], "columns": REPLACED_COLUMNS,
        "rows": [],
        "driver_calls": {"BEGIN": 1, "COMMIT": 0, "ROLLBACK": 1},
        "physical_ddl_protected": False,
        "why": "Outer transaction rolls back the DML, but legacy DDL was "
               "auto-committed: the new empty table survives; old structure, "
               "index and row 90 do not."},
    "E14": {
        "kind": "caller_responsibility",
        "errors": [["inside_outer_transaction", "OverflowError"]],
        "objects": [REPLACED_OBJECT], "columns": REPLACED_COLUMNS,
        "rows": [[1], [2]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "physical_ddl_protected": False,
        "why": "Catching the error lets the outer scope commit; even without "
               "D1 the legacy driver had already made the DDL permanent, and "
               "the commit additionally persists chunk 1."},
    "E15": {
        "kind": "correct_rollback_control",
        "errors": [["outside_transaction_scope", "OverflowError"]],
        "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
        "rows": SEED,
        "driver_calls": {"BEGIN": 1, "COMMIT": 0, "ROLLBACK": 1},
        "physical_ddl_protected": True,
        "why": "With explicit BEGIN the DDL is transactional and the escaped "
               "exception drives an outer ROLLBACK: old columns, index, row "
               "90 and the CHECK constraint all come back. This is the one "
               "fully-atomic replace configuration in the matrix."},
    "E16": {
        "kind": "caller_responsibility",
        "errors": [["inside_outer_transaction", "OverflowError"]],
        "objects": [REPLACED_OBJECT], "columns": REPLACED_COLUMNS,
        "rows": [[1], [2]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "physical_ddl_protected": True,
        "why": "Same begin mode as E15, but the caller catches and completes "
               "the scope, so the outer transaction commits the replaced "
               "table and chunk 1 deliberately."},
    "E17": {
        "kind": "success_control",
        "errors": [], "returned": 4,
        "objects": [REPLACED_OBJECT], "columns": REPLACED_COLUMNS,
        "rows": [[-3], [1], [2], [4]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "physical_ddl_protected": False,
        "why": "The replacement table has no CHECK constraint, so -3 is "
               "accepted; replace never inherits the old table's constraints."},
    "E18": {
        "kind": "success_control",
        "errors": [], "returned": 4,
        "objects": [REPLACED_OBJECT], "columns": REPLACED_COLUMNS,
        "rows": [[1], [2], [3], [4]],
        "driver_calls": {"BEGIN": 1, "COMMIT": 1, "ROLLBACK": 0},
        "physical_ddl_protected": False,
        "why": "Successful replace: legacy DDL auto-commit followed by one DML "
               "commit; the index is intentionally absent."},
}

REVIEW_REFERENCES = {
    "E01": "REVIEW.md#E01", "E02": "REVIEW.md#E02", "E03": "REVIEW.md#E03",
    "E04": "REVIEW.md#E04", "E05": "REVIEW.md#E05", "E06": "REVIEW.md#E06",
    "E07": "REVIEW.md#E07", "E08": "REVIEW.md#E08", "E09": "REVIEW.md#E09",
    "E10": "REVIEW.md#E10", "E11": "REVIEW.md#E11", "E12": "REVIEW.md#E12",
    "E13": "REVIEW.md#E13", "E14": "REVIEW.md#E14", "E15": "REVIEW.md#E15",
    "E16": "REVIEW.md#E16", "E17": "REVIEW.md#E17", "E18": "REVIEW.md#E18",
}


def _driver_calls(observation):
    sql = [item["sql"].strip().upper() for item in observation["trace"]
           if item["layer"] == "sqlite"]
    return {
        "BEGIN": sum(1 for s in sql if s.startswith("BEGIN")),
        "COMMIT": sum(1 for s in sql if s == "COMMIT"),
        "ROLLBACK": sum(1 for s in sql if s == "ROLLBACK"),
    }


def _errors(observation):
    return [[item["where"], item["type"]] for item in observation["errors"]]


def _physical_ddl_protected(observation):
    """Report whether DROP/CREATE ran while a sqlite3 transaction was open."""
    ddl = [item for item in observation["trace"]
           if item["layer"] == "sqlite"
           and ("DROP TABLE" in item["sql"].upper()
                or item["sql"].strip().upper().startswith("CREATE TABLE"))]
    if not ddl:
        return None
    return all(item["active_before"] for item in ddl)


def build_record(case):
    cid = case["id"].split("-", 1)[0]
    expectation = EXPECTATIONS[cid]
    observation = run_case(dict(case["spec"]))
    actual = {
        "errors": _errors(observation),
        "returned": observation["returned"],
        "objects": observation["after"]["objects"],
        "columns": observation["after"]["columns"],
        "rows": observation["after"]["rows"],
        "driver_calls": _driver_calls(observation),
        "physical_ddl_protected": _physical_ddl_protected(observation),
    }
    promised = None
    if expectation["kind"] == "defect":
        # The independently-derived atomic-write promise that the pinned
        # implementation fails to keep.  This is not backfilled from output.
        if expectation["defect"] == "D1":
            promised = {
                "rows": _promised_append_rows(case["spec"])
                if expectation.get("mode") != "replace" else SEED,
                "driver_calls": {"BEGIN": 1, "COMMIT": 0, "ROLLBACK": 1},
            }
        if expectation["defect"] == "D2":
            promised = {
                "objects": ORIGINAL_OBJECTS, "columns": ORIGINAL_COLUMNS,
                "rows": SEED,
                "driver_calls": {"BEGIN": 1, "COMMIT": 0, "ROLLBACK": 1},
            }
    return {
        "id": case["id"],
        "spec": case["spec"],
        "expectation": expectation,
        "atomic_promise": promised,
        "review_reference": REVIEW_REFERENCES[cid],
        "observation": {
            "versions": observation["versions"],
            "actual": actual,
            "before": observation["before"],
            "raw_trace": observation["trace"],
            "raw_errors": observation["errors"],
        },
    }


def _promised_append_rows(spec):
    # Atomic failure promise for append: none of the frame's rows survive;
    # the pre-existing seed row (90, 'old') is untouched.  The spec argument
    # documents that this promise is a function of the input, not the output.
    assert spec["if_exists"] == "append"
    return [list(row) for row in SEED]


def compare(record):
    """Compare independent expectation (or, for defects, the atomic promise)
    against the live observation.  physical_ddl_protected is an explanatory
    annotation and is validated separately in verify_ddl_boundary."""
    exp, act = record["expectation"], record["observation"]["actual"]
    target = record["atomic_promise"] if record["atomic_promise"] else exp
    mismatches = []
    for key in ("errors", "objects", "columns", "rows", "driver_calls"):
        if key not in target:
            continue
        if target[key] != act[key]:
            mismatches.append({"field": key, "expected": target[key],
                               "actual": act[key]})
    if "returned" in target and target["returned"] != act["returned"]:
        mismatches.append({"field": "returned", "expected": target["returned"],
                           "actual": act["returned"]})
    return mismatches


def verify_ddl_boundary(record):
    exp = record["expectation"]
    if "physical_ddl_protected" not in exp:
        return True
    return exp["physical_ddl_protected"] == \
        record["observation"]["actual"]["physical_ddl_protected"]


def run():
    records = [build_record(case) for case in CASES]
    payload = {
        "note": "Expectations are derived independently; observations come "
                "from probe.run_case against pinned pandas 2.2.2 / "
                "SQLAlchemy 2.0.35. Defect cases are expected to mismatch "
                "the atomic-write promise, not to pass.",
        "records": records,
    }
    EVIDENCE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for record in records:
        kind = record["expectation"]["kind"]
        mismatches = compare(record)
        if kind == "defect":
            status = "DEFECT-REPRODUCES" if mismatches else "UNEXPECTED-PASS"
        else:
            status = "MATCH" if not mismatches else "UNEXPECTED-MISMATCH"
        print(f"{record['id']:<48} {status}")
    print(f"\nwrote {EVIDENCE_PATH.relative_to(ROOT)} ({len(records)} cases)")


def verify():
    # Preserve the retained upstream/normal regression as part of verification.
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from source_manifest import verify_sources
    from upstream_regression import check_transaction_rollback
    verify_sources()
    engine = create_engine("sqlite://", poolclass=StaticPool)
    try:
        check_transaction_rollback(engine)
    finally:
        engine.dispose()
    print("UPSTREAM_TRANSACTION_REGRESSION_OK")

    if not EVIDENCE_PATH.exists():
        raise SystemExit("evidence.json missing; run: "
                         "python3 bootstrap.py review_evidence.py run")
    saved = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    saved_by_id = {record["id"]: record for record in saved["records"]}
    saved_ids = set(saved_by_id)
    live_ids = {case["id"] for case in CASES}
    assert saved_ids == live_ids, ("case set changed", saved_ids ^ live_ids)

    review_text = (ROOT / "REVIEW.md").read_text(encoding="utf-8")
    failures = 0
    for case in CASES:
        record = build_record(case)
        old = saved_by_id[case["id"]]
        mismatches = compare(record)
        kind = record["expectation"]["kind"]

        # Re-run must reproduce the exact saved observation.
        saved_actual = old["observation"]["actual"]
        if record["observation"]["actual"] != saved_actual:
            failures += 1
            print(f"FAIL {case['id']}: live observation drifted from "
                  f"evidence.json")
            continue

        # The review must locate every case.
        if record["review_reference"].split("#", 1)[1] not in review_text:
            failures += 1
            print(f"FAIL {case['id']}: missing anchor in REVIEW.md")
            continue

        # DDL/DML physical-transaction annotation must hold on re-run.
        if not verify_ddl_boundary(record):
            failures += 1
            print(f"FAIL {case['id']}: physical DDL boundary annotation "
                  f"no longer matches the live driver trace")
            continue

        if kind == "defect":
            # Confirmed defects must keep reproducing against the
            # atomic-write expectation; a silent fix would change ownership
            # semantics and has to be reviewed before acceptance.
            if not mismatches:
                failures += 1
                print(f"FAIL {case['id']}: documented defect no longer "
                      f"reproduces; re-review before accepting")
            else:
                print(f"OK   {case['id']:<44} defect still reproduces "
                      f"({len(mismatches)} documented field(s))")
        else:
            if mismatches:
                failures += 1
                print(f"FAIL {case['id']}: {mismatches}")
            else:
                print(f"OK   {case['id']:<44} matches independent expectation")
    if failures:
        raise SystemExit(f"verify failed: {failures} problem(s)")
    print("\nVERIFY_OK: saved evidence reproducible; REVIEW.md covers every "
          "case; confirmed defects still reproduce")


def demo():
    selected = ("E01", "E02", "E11", "E13", "E15", "E06")
    by_id = {case["id"].split("-", 1)[0]: case for case in CASES}
    for cid in selected:
        record = build_record(by_id[cid])
        obs = record["observation"]
        print("=" * 78)
        print(record["id"])
        print("spec      :", json.dumps(record["spec"], ensure_ascii=False))
        print("errors    :", obs["raw_errors"] and [
            (e["where"], e["type"]) for e in obs["raw_errors"]] or [])
        print("before    : objects=", [o[1] for o in obs["before"]["objects"]],
              " columns=", [c[1] for c in obs["before"]["columns"]],
              " rows=", obs["before"]["rows"])
        print("after     : objects=", [o[1] for o in obs["actual"]["objects"]],
              " columns=", [c[1] for c in obs["actual"]["columns"]],
              " rows=", obs["actual"]["rows"])
        print("driver    :", obs["actual"]["driver_calls"],
              " ddl_inside_physical_tx=",
              obs["actual"]["physical_ddl_protected"])
        print("why       :", record["expectation"]["why"])
    print("=" * 78)
    print("DEMO_OK: success, engine-owner partial commit, legacy DDL loss, "
          "outer rollback, explicit-BEGIN atomic replace, caught-and-committed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "verify", "demo"))
    args = parser.parse_args()
    {"run": run, "verify": verify, "demo": demo}[args.command]()


if __name__ == "__main__":
    main()
