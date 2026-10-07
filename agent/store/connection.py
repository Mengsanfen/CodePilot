"""DB-API adapter for application-owned SQLite/PostgreSQL statements."""
from __future__ import annotations

import sqlite3
from pathlib import Path


class PostgresConnection:
    dialect = "postgres"

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.conninfo import conninfo_to_dict
        options = conninfo_to_dict(url).get('options', '')
        self.raw = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5,
                                  options=options + ' -c statement_timeout=5000 -c lock_timeout=5000')

    def execute(self, sql: str, parameters=()):
        if sql.lstrip().upper().startswith(('CREATE ', 'ALTER ')):
            self.raw.execute('SELECT pg_advisory_lock(145887351)')
            try:
                return self.raw.execute(sql.replace('?', '%s'), parameters)
            finally:
                self.raw.execute('SELECT pg_advisory_unlock(145887351)')
        return self.raw.execute(sql.replace("?", "%s"), parameters)

    def executescript(self, sql: str):
        for statement in sql.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def connect_database(target: str | Path):
    if str(target).startswith(("postgres://", "postgresql://")):
        return PostgresConnection(str(target))
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection
