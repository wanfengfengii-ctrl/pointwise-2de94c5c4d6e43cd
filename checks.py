import argparse
import json
import sqlite3
from copy import deepcopy

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from probe import run_case
from samples import CASES
from source_manifest import verify_sources
from upstream_regression import check_transaction_rollback

def normal():
    verify_sources()
    engine = create_engine("sqlite://", poolclass=StaticPool)
    try:
        check_transaction_rollback(engine)
    finally:
        engine.dispose()
    result = run_case(CASES["normal"])
    assert result["errors"] == []
    assert result["returned"] == 4
    assert result["after"]["rows"] == [[1, "old"], [2, "old"], [3, "old"], [4, "old"], [90, "old"]]
    assert result["after"]["objects"] == result["before"]["objects"]
    assert result["after"]["columns"] == result["before"]["columns"]
    statements = [item["sql"].strip().upper() for item in result["trace"] if item["layer"] == "sqlite"]
    assert any(item.startswith("INSERT") for item in statements)
    assert "COMMIT" in statements
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("NORMAL_REGRESSION_OK")

def incident():
    result = run_case(deepcopy(CASES["incident"]))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert result["errors"], "incident must reach the database constraint"
    if result["before"] != result["after"]:
        raise AssertionError("TX_ATOMICITY_MISMATCH: a failed write changed the database")
    print("INCIDENT_ATOMIC")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["normal", "incident"])
    args = parser.parse_args()
    if args.command == "normal":
        normal()
    else:
        incident()
