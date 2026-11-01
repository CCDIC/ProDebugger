"""SQLite synthetic CTE reads must not widen access to physical tables."""
import sqlite3
import unittest
from unittest.mock import patch

from checks.test_query_backend import source
from prodebugger.debug_tools import DebugTools
from prodebugger.query_dsl import execute
from prodebugger.record_bridge import build_bundle


class QueryAuthorizerChecks(unittest.TestCase):
    def test_cte_count_allows_only_synthetic_cte_reads(self):
        connect = sqlite3.connect
        callbacks = []
        reads = []

        class InstrumentedConnection(sqlite3.Connection):
            def set_authorizer(self, callback):
                callbacks.append(callback)

                def observed(*args):
                    if args[0] == sqlite3.SQLITE_READ:
                        reads.append(args)
                    return callback(*args)

                return super().set_authorizer(observed)

        def instrumented(*args, **kwargs):
            kwargs["factory"] = InstrumentedConnection
            return connect(*args, **kwargs)

        tools = DebugTools(build_bundle(source()))
        sql = ("WITH bad AS (SELECT * FROM SEARCH_TEXT('search','plan')), "
               "good AS (SELECT * FROM SEARCH_TEXT('apple','memory')) "
               "SELECT count(*) AS n FROM bad UNION ALL "
               "SELECT 999 AS n WHERE NOT EXISTS (SELECT 1 FROM bad) UNION ALL "
               "SELECT count(*) AS n FROM good")
        with patch("prodebugger.query_dsl.sqlite3.connect", instrumented):
            result = execute(tools, sql)
        self.assertNotIn("error", result)
        self.assertEqual(result["rows"], [{"n": 3}])
        self.assertEqual([branch["branch"] for branch in result["skipped_branches"]], [1, 2])
        self.assertTrue(reads)
        authorize = callbacks[-1]
        self.assertEqual(authorize(sqlite3.SQLITE_READ, "good", "", None, None), sqlite3.SQLITE_OK)
        for table, column, database in (("good", "text", None), ("good", "", "main"),
                                        ("steps", "", None), ("facts", "span", "main"),
                                        ("sqlite_master", "sql", "main")):
            with self.subTest(table=table, column=column, database=database):
                self.assertEqual(authorize(sqlite3.SQLITE_READ, table, column, database, None),
                                 sqlite3.SQLITE_DENY)
        self.assertEqual(authorize(sqlite3.SQLITE_UPDATE, "goals", "name", "main", None),
                         sqlite3.SQLITE_DENY)

    def test_cte_named_like_raw_table_cannot_expose_raw_sources(self):
        tools = DebugTools(build_bundle(source()))
        for sql in (
            "WITH allowed AS (SELECT * FROM steps) SELECT count(*) FROM allowed UNION ALL SELECT count(*) FROM SEARCH_TEXT('apple','memory')",
            "WITH steps AS (SELECT * FROM SEARCH_TEXT('apple','memory')) SELECT count(*) FROM steps UNION ALL SELECT count(*) FROM facts",
        ):
            with self.subTest(sql=sql):
                result = execute(tools, sql)
                self.assertIn("error", result)
                self.assertEqual(result["rows"], [])


if __name__ == "__main__":
    unittest.main()
