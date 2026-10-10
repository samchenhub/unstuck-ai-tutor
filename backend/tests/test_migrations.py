import sqlite3

from unstuck.db import connect, repo

OLD_VIDEOS_SQL = """
CREATE TABLE videos (
    id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '', duration REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'importing', ask_ready INTEGER NOT NULL DEFAULT 0,
    stage_states TEXT NOT NULL DEFAULT '{}', visual_mode TEXT NOT NULL DEFAULT 'full',
    outline TEXT, error_code TEXT, error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def test_old_database_is_migrated(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.executescript(OLD_VIDEOS_SQL)
    raw.execute("INSERT INTO videos (id, url, title) VALUES ('dQw4w9WgXcQ', 'u', 'Old video')")
    raw.commit()
    raw.close()

    conn = connect(db, 64)
    video = repo.get_video(conn, "dQw4w9WgXcQ")
    assert video.title == "Old video"
    assert video.source == "youtube"
    assert video.course_id is None and video.part is None
    assert video.part_title == "" and video.parts_total == 1 and video.thumbnail == ""
    conn.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "x.db"
    connect(db, 64).close()
    conn = connect(db, 64)
    names = [r["name"] for r in conn.execute("PRAGMA table_info(videos)")]
    assert names.count("source") == 1
    conn.close()


def test_insert_and_update_bilibili_video(conn):
    assert repo.insert_video(
        conn, "BV1Ex4y1G7nG_p2", "https://www.bilibili.com/video/BV1Ex4y1G7nG?p=2",
        source="bilibili", course_id="BV1Ex4y1G7nG", part=2,
    )
    repo.update_video(conn, "BV1Ex4y1G7nG_p2", part_title="极限", parts_total=7, thumbnail="https://i0.hdslb.com/a.jpg")
    video = repo.get_video(conn, "BV1Ex4y1G7nG_p2")
    assert (video.source, video.course_id, video.part) == ("bilibili", "BV1Ex4y1G7nG", 2)
    assert (video.part_title, video.parts_total, video.thumbnail) == ("极限", 7, "https://i0.hdslb.com/a.jpg")
