import pytest

from unstuck.db import repo
from unstuck.llm import FakeLLM
from unstuck.tutor import answer
from unstuck.tutor.answer import SYSTEM_PROMPT, prepare_messages, stream_answer


@pytest.fixture
def no_live_frames(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("live frame must not be captured here")

    monkeypatch.setattr(answer, "live_frame_description", fail)


def user_content(messages):
    assert messages[-1]["role"] == "user"
    return messages[-1]["content"]


def test_prepare_messages_structure(conn, fake_llm, settings, seeded_video, no_live_frames):
    messages = prepare_messages(conn, fake_llm, settings, seeded_video, "Why the cosine?", 110.0)

    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    content = user_content(messages)
    assert content.startswith("<video_data>")
    assert "paused the video at [01:50] and asks:\nWhy the cosine?" in content
    assert "dot product geometry cosine" in content          # window
    assert "nearest keyframe, [01:40]" in content             # keyframe 10 s before t
    assert "Slide showing the dot product formula" in content
    assert "[00:00] Vectors: Vectors and the dot product  <-- the learner is here" in content
    assert "(covered later in the video)" in content          # retrieved after t


def test_live_frame_used_when_keyframe_is_stale(conn, fake_llm, settings, seeded_video, monkeypatch):
    calls = []

    def live(conn_, llm_, settings_, video_id, t, question):
        calls.append((video_id, t, question))
        return "Whiteboard with a 2x2 matrix"

    monkeypatch.setattr(answer, "live_frame_description", live)
    content = user_content(prepare_messages(conn, fake_llm, settings, seeded_video, "What is this?", 190.0))

    assert calls == [(seeded_video, 190.0, "What is this?")]
    assert "captured live" in content
    assert "Whiteboard with a 2x2 matrix" in content


def test_live_frame_failure_falls_back_to_nearest_keyframe(conn, fake_llm, settings, seeded_video, monkeypatch):
    monkeypatch.setattr(answer, "live_frame_description", lambda *a: None)
    content = user_content(prepare_messages(conn, fake_llm, settings, seeded_video, "What is this?", 190.0))
    assert "nearest keyframe, [01:40]" in content


def test_use_visual_false_drops_screen_content(conn, fake_llm, settings, seeded_video, no_live_frames):
    content = user_content(
        prepare_messages(conn, fake_llm, settings, seeded_video, "q", 110.0, use_visual=False)
    )
    assert "Not available" in content
    assert "Slide showing the dot product formula" not in content


def test_transcript_only_video(conn, fake_llm, settings, seeded_video, no_live_frames):
    repo.update_video(conn, seeded_video, visual_mode="transcript_only")
    content = user_content(prepare_messages(conn, fake_llm, settings, seeded_video, "q", 110.0))
    assert "Not available" in content


def test_history_from_db_and_override(conn, fake_llm, settings, seeded_video, no_live_frames):
    repo.add_chat(conn, seeded_video, "user", "earlier question", 50.0)
    repo.add_chat(conn, seeded_video, "assistant", "earlier answer", 50.0)

    messages = prepare_messages(conn, fake_llm, settings, seeded_video, "next", 110.0)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "earlier question"

    messages = prepare_messages(conn, fake_llm, settings, seeded_video, "next", 110.0, history=[])
    assert [m["role"] for m in messages] == ["system", "user"]


def test_stream_answer_happy_path(conn, settings, seeded_video, no_live_frames):
    llm = FakeLLM(chat_responses=["The cosine measures the angle, see [01:40]."], embed_dim=settings.embed_dim)

    events = list(stream_answer(conn, llm, settings, seeded_video, "Why cosine?", 110.0))

    assert "".join(e["text"] for e in events if e["type"] == "delta") == "The cosine measures the angle, see [01:40]."
    assert events[-1]["type"] == "done"
    user, assistant = repo.list_chats(conn, seeded_video)
    assert (user.role, user.content, user.t_at_ask) == ("user", "Why cosine?", 110.0)
    assert assistant.content == "The cosine measures the angle, see [01:40]."
    assert assistant.complete is True
    assert events[-1]["chat_id"] == assistant.id


def test_stream_failure_keeps_partial_answer(conn, settings, seeded_video, no_live_frames):
    llm = FakeLLM(chat_responses=[["Hel", "lo", RuntimeError("cut")]], embed_dim=settings.embed_dim)

    events = list(stream_answer(conn, llm, settings, seeded_video, "q", 110.0))

    assert [e["type"] for e in events] == ["delta", "delta", "error"]
    assistant = repo.list_chats(conn, seeded_video)[-1]
    assert assistant.content == "Hello" and assistant.complete is False
    assert events[-1]["chat_id"] == assistant.id


def test_client_disconnect_keeps_partial_answer(conn, settings, seeded_video, no_live_frames):
    llm = FakeLLM(chat_responses=[["first ", "second"]], embed_dim=settings.embed_dim)
    stream = stream_answer(conn, llm, settings, seeded_video, "q", 110.0)

    assert next(stream) == {"type": "delta", "text": "first "}
    stream.close()

    assistant = repo.list_chats(conn, seeded_video)[-1]
    assert assistant.content == "first " and assistant.complete is False


def test_prepare_failure_yields_error_without_saving(conn, settings, fake_llm):
    events = list(stream_answer(conn, fake_llm, settings, "missingvid1", "q", 1.0))
    assert [e["type"] for e in events] == ["error"]
    assert repo.list_chats(conn, "missingvid1") == []
