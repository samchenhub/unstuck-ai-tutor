import re
import sqlite3
from pathlib import Path

import sqlite_vec

from unstuck.db.schema import SCHEMA, VIDEO_COLUMNS_ADDED, vec_table_sql


def connect(db_path: Path, embed_dim: int) -> sqlite3.Connection:
    """Open the database, load sqlite-vec and make sure all tables exist.

    check_same_thread=False because FastAPI iterates streaming responses on
    worker threads; each connection is still used by one request at a time.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    if not hasattr(conn, "enable_load_extension"):
        conn.close()
        raise RuntimeError(
            "这个 Python 不支持加载 SQLite 扩展（例如 python.org 的 macOS 安装包），Unstuck 需要它。"
            "请在 backend 目录运行 `uv python install 3.12`，再运行 `uv sync` 后重试。"
        )
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate_videos(conn)
    _check_embed_dim(conn, db_path, embed_dim)
    conn.execute(vec_table_sql(embed_dim))
    conn.commit()
    return conn


def _migrate_videos(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    for name, ddl in VIDEO_COLUMNS_ADDED.items():
        if name in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {ddl}")
        except sqlite3.OperationalError as exc:
            # Another connection may have added it a moment ago (startup races).
            if "duplicate column" not in str(exc):
                raise


def _check_embed_dim(conn: sqlite3.Connection, db_path: Path, embed_dim: int) -> None:
    """A vec0 table's dimension is fixed at creation; fail loudly instead of on first insert."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'vec_segments'").fetchone()
    match = re.search(r"float\[(\d+)\]", row["sql"]) if row else None
    if match and int(match.group(1)) != embed_dim:
        conn.close()
        raise RuntimeError(
            f"vec_segments was created with dimension {match.group(1)} but EMBED_DIM is {embed_dim}; "
            f"delete {db_path} or restore EMBED_DIM"
        )
