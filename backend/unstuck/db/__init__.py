import sqlite3
from pathlib import Path

import sqlite_vec

from unstuck.db.schema import SCHEMA, vec_table_sql


def connect(db_path: Path, embed_dim: int) -> sqlite3.Connection:
    """Open the database, load sqlite-vec and make sure all tables exist.

    check_same_thread=False because FastAPI iterates streaming responses on
    worker threads; each connection is still used by one request at a time.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.execute(vec_table_sql(embed_dim))
    conn.commit()
    return conn
