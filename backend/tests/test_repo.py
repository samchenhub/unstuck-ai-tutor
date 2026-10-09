import pytest

from unstuck.db import connect, repo
from unstuck.models import Cue, FrameDescription, NewKeyframe, Segment

VID = "abcdefghijk"
OTHER = "zzzzzzzzzzz"


def unit(dim, i):
    v = [0.0] * dim
    v[i] = 1.0
    return v


def test_vec_extension_loaded(conn):
    (version,) = conn.execute("select vec_version()").fetchone()
    assert version.startswith("v")


def test_connect_rejects_embedding_dimension_mismatch(tmp_path):
    path = tmp_path / "dim.db"
    connect(path, 64).close()
    with pytest.raises(RuntimeError, match="dimension"):
        connect(path, 32)
    connect(path, 64).close()


def test_insert_and_get_video(conn):
    assert repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk") is True
    assert repo.insert_video(conn, VID, "https://youtu.be/abcdefghijk") is False
    v = repo.get_video(conn, VID)
    assert v.status == "importing"
    assert v.stage_states == {}
    assert v.visual_mode == "full"
    assert v.outline is None
    assert v.ask_ready is False


def test_get_missing_video(conn):
    assert repo.get_video(conn, "nope") is None


def test_update_video_json_and_bool_fields(conn):
    repo.insert_video(conn, VID, "u")
    repo.update_video(
        conn, VID,
        title="T",
        stage_states={"metadata": "done"},
        outline=[{"start": 0.0, "title": "Intro", "summary": ""}],
        ask_ready=True,
    )
    v = repo.get_video(conn, VID)
    assert v.title == "T"
    assert v.stage_states == {"metadata": "done"}
    assert v.outline[0]["title"] == "Intro"
    assert v.ask_ready is True


def test_update_video_rejects_unknown_field(conn):
    repo.insert_video(conn, VID, "u")
    with pytest.raises(ValueError):
        repo.update_video(conn, VID, bogus=1)


def test_list_videos_newest_first(conn):
    repo.insert_video(conn, "aaaaaaaaaaa", "u")
    repo.insert_video(conn, "bbbbbbbbbbb", "u")
    assert [v.id for v in repo.list_videos(conn)] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]


def test_cues_replace(conn):
    repo.replace_cues(conn, VID, [Cue(0, 2, "hello"), Cue(2, 4, "world")])
    repo.replace_cues(conn, VID, [Cue(0, 1, "only")])
    assert repo.get_cues(conn, VID) == [Cue(0.0, 1.0, "only")]


def test_keyframes_and_descriptions(conn):
    frames = repo.replace_keyframes(
        conn, VID, [NewKeyframe(20.0, "/f/2.jpg", "bb"), NewKeyframe(1.0, "/f/1.jpg", "aa")]
    )
    assert sorted(f.t for f in frames) == [1.0, 20.0]
    assert {f.status for f in frames} == {"pending"}
    first, second = repo.get_keyframes(conn, VID)
    repo.save_frame_description(conn, first.id, FrameDescription("formula", "Euler", "e^{i\\pi}", "first"))
    repo.save_frame_description(conn, second.id, None)

    got = repo.get_keyframes(conn, VID)
    assert got[0].t == 1.0 and got[0].status == "ok"
    assert got[0].kind == "formula" and got[0].ocr_text == "e^{i\\pi}"
    assert got[1].status == "failed"


def test_segments_and_vector_search(conn, settings):
    dim = settings.embed_dim
    segs = repo.replace_segments(conn, VID, [Segment(0, 30, "a", [1]), Segment(30, 60, "b")])
    assert all(s.id is not None for s in segs)
    assert repo.get_segments(conn, VID)[0].keyframe_ids == [1]

    repo.replace_vectors(conn, VID, [(segs[0].id, unit(dim, 0)), (segs[1].id, unit(dim, 1))])
    hits = repo.search_vectors(conn, VID, unit(dim, 1), k=2)
    assert hits[0][0] == segs[1].id
    assert hits[0][1] <= hits[1][1]


def test_vector_search_is_scoped_to_video(conn, settings):
    dim = settings.embed_dim
    (a,) = repo.replace_segments(conn, VID, [Segment(0, 30, "a")])
    (b,) = repo.replace_segments(conn, OTHER, [Segment(0, 30, "b")])
    repo.replace_vectors(conn, VID, [(a.id, unit(dim, 0))])
    repo.replace_vectors(conn, OTHER, [(b.id, unit(dim, 0))])
    assert [sid for sid, _ in repo.search_vectors(conn, VID, unit(dim, 0), k=5)] == [a.id]


def test_replace_segments_drops_old_vectors(conn, settings):
    dim = settings.embed_dim
    (a,) = repo.replace_segments(conn, VID, [Segment(0, 30, "a")])
    repo.replace_vectors(conn, VID, [(a.id, unit(dim, 0))])
    repo.replace_segments(conn, VID, [Segment(0, 10, "c")])
    assert repo.search_vectors(conn, VID, unit(dim, 0), k=5) == []


def test_chats_recent_in_chronological_order(conn):
    for i in range(5):
        repo.add_chat(conn, VID, "user" if i % 2 == 0 else "assistant", f"m{i}", 10.0)
    assert [c.content for c in repo.recent_chats(conn, VID, limit=3)] == ["m2", "m3", "m4"]
    assert len(repo.list_chats(conn, VID)) == 5


def test_incomplete_chat_flag(conn):
    repo.add_chat(conn, VID, "assistant", "partial", 3.0, complete=False)
    (chat,) = repo.list_chats(conn, VID)
    assert chat.complete is False
    assert chat.t_at_ask == 3.0


def test_frame_cache(conn):
    assert repo.get_frame_cache(conn, VID, 12) is None
    repo.put_frame_cache(conn, VID, 12, "desc")
    repo.put_frame_cache(conn, VID, 12, "newer")
    assert repo.get_frame_cache(conn, VID, 12) == "newer"
