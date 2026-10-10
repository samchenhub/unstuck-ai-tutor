import json
import sqlite3

from sqlite_vec import serialize_float32

from unstuck.models import Chat, Cue, FrameDescription, Keyframe, NewKeyframe, Segment, Video

_VIDEO_UPDATABLE = {
    "title", "channel", "duration", "status", "ask_ready", "stage_states",
    "visual_mode", "outline", "error_code", "error_message",
    "part_title", "parts_total", "thumbnail",
}


# ---- videos ---------------------------------------------------------------

def _video(row: sqlite3.Row) -> Video:
    return Video(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        channel=row["channel"],
        duration=row["duration"],
        status=row["status"],
        ask_ready=bool(row["ask_ready"]),
        stage_states=json.loads(row["stage_states"]),
        visual_mode=row["visual_mode"],
        outline=json.loads(row["outline"]) if row["outline"] else None,
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        source=row["source"],
        course_id=row["course_id"],
        part=row["part"],
        part_title=row["part_title"],
        parts_total=row["parts_total"],
        thumbnail=row["thumbnail"],
    )


def insert_video(
    conn: sqlite3.Connection, video_id: str, url: str, *,
    source: str = "youtube", course_id: str | None = None, part: int | None = None,
) -> bool:
    """Insert a new video row. Returns False if it already existed."""
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO videos (id, url, source, course_id, part) VALUES (?, ?, ?, ?, ?)",
            (video_id, url, source, course_id, part),
        )
    return cur.rowcount == 1


def get_video(conn: sqlite3.Connection, video_id: str) -> Video | None:
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    return _video(row) if row else None


def list_videos(conn: sqlite3.Connection) -> list[Video]:
    rows = conn.execute("SELECT * FROM videos ORDER BY created_at DESC, rowid DESC").fetchall()
    return [_video(r) for r in rows]


def update_video(conn: sqlite3.Connection, video_id: str, **fields) -> None:
    unknown = set(fields) - _VIDEO_UPDATABLE
    if unknown:
        raise ValueError(f"cannot update video fields: {sorted(unknown)}")
    if not fields:
        return
    columns, values = [], []
    for key, value in fields.items():
        if key in ("stage_states", "outline") and value is not None:
            value = json.dumps(value, ensure_ascii=False)
        if key == "ask_ready":
            value = int(bool(value))
        columns.append(f"{key} = ?")
        values.append(value)
    with conn:
        conn.execute(f"UPDATE videos SET {', '.join(columns)} WHERE id = ?", (*values, video_id))


# ---- cues -----------------------------------------------------------------

def replace_cues(conn: sqlite3.Connection, video_id: str, cues: list[Cue]) -> None:
    with conn:
        conn.execute("DELETE FROM cues WHERE video_id = ?", (video_id,))
        conn.executemany(
            "INSERT INTO cues (video_id, idx, t_start, t_end, text) VALUES (?, ?, ?, ?, ?)",
            [(video_id, i, c.start, c.end, c.text) for i, c in enumerate(cues)],
        )


def get_cues(conn: sqlite3.Connection, video_id: str) -> list[Cue]:
    rows = conn.execute(
        "SELECT t_start, t_end, text FROM cues WHERE video_id = ? ORDER BY idx", (video_id,)
    ).fetchall()
    return [Cue(r["t_start"], r["t_end"], r["text"]) for r in rows]


# ---- keyframes ------------------------------------------------------------

def _keyframe(row: sqlite3.Row) -> Keyframe:
    return Keyframe(
        id=row["id"], video_id=row["video_id"], t=row["t"], image_path=row["image_path"],
        phash=row["phash"], summary=row["summary"], ocr_text=row["ocr_text"], kind=row["kind"],
        change_note=row["change_note"], status=row["status"],
    )


def replace_keyframes(conn: sqlite3.Connection, video_id: str, frames: list[NewKeyframe]) -> list[Keyframe]:
    with conn:
        conn.execute("DELETE FROM keyframes WHERE video_id = ?", (video_id,))
        conn.executemany(
            "INSERT INTO keyframes (video_id, t, image_path, phash) VALUES (?, ?, ?, ?)",
            [(video_id, f.t, f.image_path, f.phash) for f in frames],
        )
    return get_keyframes(conn, video_id)


def get_keyframes(conn: sqlite3.Connection, video_id: str) -> list[Keyframe]:
    rows = conn.execute("SELECT * FROM keyframes WHERE video_id = ? ORDER BY t", (video_id,)).fetchall()
    return [_keyframe(r) for r in rows]


def save_frame_description(conn: sqlite3.Connection, keyframe_id: int, desc: FrameDescription | None) -> None:
    with conn:
        if desc is None:
            conn.execute("UPDATE keyframes SET status = 'failed' WHERE id = ?", (keyframe_id,))
        else:
            conn.execute(
                "UPDATE keyframes SET kind = ?, summary = ?, ocr_text = ?, change_note = ?, status = 'ok' "
                "WHERE id = ?",
                (desc.kind, desc.summary, desc.ocr_text, desc.change_note, keyframe_id),
            )


# ---- segments & vectors ---------------------------------------------------

def _segment_ids(conn: sqlite3.Connection, video_id: str) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM segments WHERE video_id = ?", (video_id,))]


def replace_segments(conn: sqlite3.Connection, video_id: str, segments: list[Segment]) -> list[Segment]:
    """Replace a video's segments (and drop their vectors). Returns segments with ids."""
    with conn:
        conn.executemany(
            "DELETE FROM vec_segments WHERE segment_id = ?", [(i,) for i in _segment_ids(conn, video_id)]
        )
        conn.execute("DELETE FROM segments WHERE video_id = ?", (video_id,))
        for seg in segments:
            conn.execute(
                "INSERT INTO segments (video_id, t_start, t_end, transcript, keyframe_ids) VALUES (?, ?, ?, ?, ?)",
                (video_id, seg.start, seg.end, seg.transcript, json.dumps(seg.keyframe_ids)),
            )
    return get_segments(conn, video_id)


def get_segments(conn: sqlite3.Connection, video_id: str) -> list[Segment]:
    rows = conn.execute(
        "SELECT * FROM segments WHERE video_id = ? ORDER BY t_start", (video_id,)
    ).fetchall()
    return [
        Segment(
            start=r["t_start"], end=r["t_end"], transcript=r["transcript"],
            keyframe_ids=json.loads(r["keyframe_ids"]), id=r["id"],
        )
        for r in rows
    ]


def replace_vectors(conn: sqlite3.Connection, video_id: str, items: list[tuple[int, list[float]]]) -> None:
    with conn:
        conn.executemany(
            "DELETE FROM vec_segments WHERE segment_id = ?", [(i,) for i in _segment_ids(conn, video_id)]
        )
        conn.executemany(
            "INSERT INTO vec_segments (segment_id, video_id, embedding) VALUES (?, ?, ?)",
            [(sid, video_id, serialize_float32(vec)) for sid, vec in items],
        )


def search_vectors(conn: sqlite3.Connection, video_id: str, query: list[float], k: int) -> list[tuple[int, float]]:
    rows = conn.execute(
        "SELECT segment_id, distance FROM vec_segments "
        "WHERE embedding MATCH ? AND k = ? AND video_id = ? ORDER BY distance",
        (serialize_float32(query), k, video_id),
    ).fetchall()
    return [(r["segment_id"], r["distance"]) for r in rows]


# ---- chats ----------------------------------------------------------------

def _chat(row: sqlite3.Row) -> Chat:
    return Chat(
        id=row["id"], video_id=row["video_id"], role=row["role"], content=row["content"],
        t_at_ask=row["t_at_ask"], complete=bool(row["complete"]), created_at=row["created_at"],
    )


def add_chat(
    conn: sqlite3.Connection, video_id: str, role: str, content: str,
    t_at_ask: float | None, complete: bool = True,
) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO chats (video_id, role, content, t_at_ask, complete) VALUES (?, ?, ?, ?, ?)",
            (video_id, role, content, t_at_ask, int(complete)),
        )
    return cur.lastrowid


def list_chats(conn: sqlite3.Connection, video_id: str) -> list[Chat]:
    rows = conn.execute("SELECT * FROM chats WHERE video_id = ? ORDER BY id", (video_id,)).fetchall()
    return [_chat(r) for r in rows]


def recent_chats(conn: sqlite3.Connection, video_id: str, limit: int) -> list[Chat]:
    rows = conn.execute(
        "SELECT * FROM chats WHERE video_id = ? ORDER BY id DESC LIMIT ?", (video_id, limit)
    ).fetchall()
    return [_chat(r) for r in reversed(rows)]


# ---- frame cache ----------------------------------------------------------

def get_frame_cache(conn: sqlite3.Connection, video_id: str, second: int) -> str | None:
    row = conn.execute(
        "SELECT description FROM frame_cache WHERE video_id = ? AND second = ?", (video_id, second)
    ).fetchone()
    return row["description"] if row else None


def put_frame_cache(conn: sqlite3.Connection, video_id: str, second: int, description: str) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO frame_cache (video_id, second, description) VALUES (?, ?, ?)",
            (video_id, second, description),
        )
