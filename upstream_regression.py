"""Specialize pandas 2.2.2's original rollback test to its SQLAlchemy SQLite engine.
Source: pandas/tests/io/test_sql.py:test_transaction_rollback, lines 3155-3193.
The multi-backend pytest fixture is replaced by an engine supplied by the caller;
the rollback/commit assertions and their order are preserved.
"""
from pandas.io.sql import pandasSQL_builder
from sqlalchemy import text

def check_transaction_rollback(engine):
    with pandasSQL_builder(engine) as pandasSQL:
        with pandasSQL.run_transaction() as trans:
            trans.execute(text("CREATE TABLE test_trans (A INT, B TEXT)"))

        class DummyException(Exception):
            pass

        ins_sql = text("INSERT INTO test_trans (A,B) VALUES (1, 'blah')")
        try:
            with pandasSQL.run_transaction() as trans:
                trans.execute(ins_sql)
                raise DummyException("error")
        except DummyException:
            pass
        with pandasSQL.run_transaction():
            res = pandasSQL.read_query("SELECT * FROM test_trans")
        assert len(res) == 0

        with pandasSQL.run_transaction() as trans:
            trans.execute(ins_sql)
            res2 = pandasSQL.read_query("SELECT * FROM test_trans")
        assert len(res2) == 1
