SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'importing',
    ask_ready INTEGER NOT NULL DEFAULT 0,
    stage_states TEXT NOT NULL DEFAULT '{}',
    visual_mode TEXT NOT NULL DEFAULT 'full',
    outline TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cues (
    video_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (video_id, idx)
);

CREATE TABLE IF NOT EXISTS keyframes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    t REAL NOT NULL,
    image_path TEXT NOT NULL,
    phash TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    ocr_text TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'other',
    change_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_keyframes_video_t ON keyframes (video_id, t);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    transcript TEXT NOT NULL,
    keyframe_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_segments_video_start ON segments (video_id, t_start);

CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    t_at_ask REAL,
    complete INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chats_video ON chats (video_id, id);

CREATE TABLE IF NOT EXISTS frame_cache (
    video_id TEXT NOT NULL,
    second INTEGER NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (video_id, second)
);
"""

# Columns added after Phase 1. connect() adds any that are missing, so old databases keep working.
VIDEO_COLUMNS_ADDED = {
    "source": "TEXT NOT NULL DEFAULT 'youtube'",
    "course_id": "TEXT",
    "part": "INTEGER",
    "part_title": "TEXT NOT NULL DEFAULT ''",
    "parts_total": "INTEGER NOT NULL DEFAULT 1",
    "thumbnail": "TEXT NOT NULL DEFAULT ''",
    "transcribed_until": "REAL NOT NULL DEFAULT 0",
}


def vec_table_sql(dim: int) -> str:
    return (
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_segments USING vec0("
        "segment_id integer primary key, "
        "video_id text partition key, "
        f"embedding float[{dim}])"
    )
