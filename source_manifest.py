"""Read and fingerprint the installed, unmodified upstream source."""
import argparse
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import platform
import sqlite3

import numpy
import pandas
import sqlalchemy

MODULES = {
    "pandas.core.generic": "504217660fbe1aa74ff98b31bea093bd9043cd61c35aba272073464f38e46f22",
    "pandas.io.sql": "01ece5cc6c3be947a31d56e5bb5c7db46a5ec7a6e64a0dbf42ebaf0559d27f48",
    "sqlalchemy.engine.base": "7eb592326b777657a7b581f840d3777228dd1b3a7c35bba70a89546705ac5a02",
    "sqlalchemy.engine.default": "db0c0a29db1a81bdd04da8d1484c3c1e5f849d0f8b991c72f36db1386c9e9c77",
    "sqlalchemy.dialects.sqlite.pysqlite": "683a78ee7d09e74f64976843721a1a04a5c441566d92ec79e03c1f2b2b5401ee"
}
SYMBOLS = [
    ("pandas.core.generic", "NDFrame.to_sql"),
    ("pandas.io.sql", "to_sql"),
    ("pandas.io.sql", "pandasSQL_builder"),
    ("pandas.io.sql", "SQLDatabase.__init__"),
    ("pandas.io.sql", "SQLDatabase.__exit__"),
    ("pandas.io.sql", "SQLDatabase.run_transaction"),
    ("pandas.io.sql", "SQLDatabase.prep_table"),
    ("pandas.io.sql", "SQLDatabase.to_sql"),
    ("pandas.io.sql", "SQLDatabase.drop_table"),
    ("pandas.io.sql", "SQLTable.create"),
    ("pandas.io.sql", "SQLTable._execute_create"),
    ("pandas.io.sql", "SQLTable.insert"),
    ("pandas.io.sql", "SQLAlchemyEngine.insert_records"),
    ("sqlalchemy.engine.base", "Connection.begin"),
    ("sqlalchemy.engine.base", "Connection._begin_impl"),
    ("sqlalchemy.engine.base", "RootTransaction"),
    ("sqlalchemy.engine.default", "DefaultDialect.do_begin")
]

def verify_sources():
    assert numpy.__version__ == "1.26.4", numpy.__version__
    assert pandas.__version__ == "2.2.2", pandas.__version__
    assert sqlalchemy.__version__ == "2.0.35", sqlalchemy.__version__
    for name, expected in MODULES.items():
        module = importlib.import_module(name)
        actual = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        assert actual == expected, (name, expected, actual)

def manifest():
    verify_sources()
    symbols = []
    for module_name, name in SYMBOLS:
        obj = importlib.import_module(module_name)
        for part in name.split("."):
            obj = getattr(obj, part)
        source, start = inspect.getsourcelines(obj)
        symbols.append({"module": module_name, "symbol": name,
                        "line": start, "end_line": start + len(source) - 1})
    return {
        "repos": {"pandas": "https://github.com/pandas-dev/pandas/tree/v2.2.2",
                  "sqlalchemy": "https://github.com/sqlalchemy/sqlalchemy/tree/rel_2_0_35"},
        "versions": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                     "numpy": numpy.__version__, "pandas": pandas.__version__,
                     "sqlalchemy": sqlalchemy.__version__},
        "modules": {name: {"path": importlib.import_module(name).__file__, "sha256": value}
                    for name, value in MODULES.items()},
        "symbols": symbols
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", choices=sorted(MODULES))
    args = parser.parse_args()
    if args.module:
        verify_sources()
        path = Path(importlib.import_module(args.module).__file__)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            print(str(number) + ": " + line)
    else:
        print(json.dumps(manifest(), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
