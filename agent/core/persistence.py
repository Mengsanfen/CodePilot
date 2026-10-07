from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from agent.env_utils import get_env


def make_checkpointer(db_path: Path) -> SqliteSaver:
    """创建保存 LangGraph thread state 的 SQLite checkpointer。"""
    database_url = get_env("CODEPILOT_DATABASE_URL")
    if database_url.startswith(("postgres://", "postgresql://")):
        import psycopg
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres import PostgresSaver
        connection = psycopg.connect(database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
        saver = PostgresSaver(connection)
        connection.execute('SELECT pg_advisory_lock(145887352)')
        try:
            saver.setup()
        finally:
            connection.execute('SELECT pg_advisory_unlock(145887352)')
        return saver
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver
