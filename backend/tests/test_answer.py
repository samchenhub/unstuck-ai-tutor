import pytest

from unstuck.db import repo
from unstuck.llm import FakeLLM, ModelBusyError
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


def test_model_busy_mid_stream_reports_rate_limit_and_keeps_partial(conn, settings, seeded_video, no_live_frames):
    llm = FakeLLM(chat_responses=[["Hi", ModelBusyError("x")]], embed_dim=settings.embed_dim)

    events = list(stream_answer(conn, llm, settings, seeded_video, "q", 110.0))

    assert events[-1]["type"] == "error"
    assert events[-1]["message"] == "模型当前繁忙（免费额度限流），请稍后点击重新生成"
    assistant = repo.list_chats(conn, seeded_video)[-1]
    assert assistant.content == "Hi" and assistant.complete is False
    assert events[-1]["chat_id"] == assistant.id


def test_model_busy_while_preparing_reports_rate_limit(conn, settings, seeded_video, no_live_frames):
    llm = FakeLLM(embed_dim=settings.embed_dim)

    def busy(*args, **kwargs):
        raise ModelBusyError("x")

    llm.embed = busy
    events = list(stream_answer(conn, llm, settings, seeded_video, "q", 110.0))

    assert events == [{"type": "error", "message": "模型当前繁忙（免费额度限流），请稍后点击重新生成"}]
    assert repo.list_chats(conn, seeded_video) == []


@pytest.mark.parametrize(
    ("question", "instruction"),
    [
        ("为什么要用余弦？", "Answer in Chinese (简体中文), even though the video material may be in another language."),
        ("Why the cosine?", "Answer in the same language as the learner's question."),
        ("これは何ですか", "Answer in Japanese."),
        ("이것은 무엇입니까", "Answer in Korean."),
    ],
)
def test_answer_language_instruction_ends_the_user_message(
    conn, fake_llm, settings, seeded_video, no_live_frames, question, instruction
):
    messages = prepare_messages(conn, fake_llm, settings, seeded_video, question, 110.0)
    assert user_content(messages).endswith(f"asks:\n{question}\n\n{instruction}")


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


def test_history_skips_empty_assistant_and_its_question(conn, fake_llm, settings, seeded_video, no_live_frames):
    repo.add_chat(conn, seeded_video, "user", "q1", 50.0)
    repo.add_chat(conn, seeded_video, "assistant", "", 50.0, complete=False)
    repo.add_chat(conn, seeded_video, "user", "q2", 60.0)
    repo.add_chat(conn, seeded_video, "assistant", "a2", 60.0)

    messages = prepare_messages(conn, fake_llm, settings, seeded_video, "next", 110.0)

    assert [(m["role"], m["content"]) for m in messages[1:-1]] == [("user", "q2"), ("assistant", "a2")]


def test_history_drops_orphan_trailing_question(conn, fake_llm, settings, seeded_video, no_live_frames):
    repo.add_chat(conn, seeded_video, "user", "q1", 50.0)
    repo.add_chat(conn, seeded_video, "assistant", "a1", 50.0)
    repo.add_chat(conn, seeded_video, "user", "q3", 60.0)

    messages = prepare_messages(conn, fake_llm, settings, seeded_video, "next", 110.0)

    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert "q3" not in [m["content"] for m in messages[1:3]]


def test_history_adjacent_assistants_keep_the_later(conn, fake_llm, settings, seeded_video, no_live_frames):
    repo.add_chat(conn, seeded_video, "user", "q1", 50.0)
    repo.add_chat(conn, seeded_video, "assistant", "first", 50.0, complete=False)
    repo.add_chat(conn, seeded_video, "assistant", "second", 50.0)

    messages = prepare_messages(conn, fake_llm, settings, seeded_video, "next", 110.0)

    assert [(m["role"], m["content"]) for m in messages[1:-1]] == [("user", "q1"), ("assistant", "second")]


def failing_add_chat(monkeypatch, fail_on_call):
    real = answer.repo.add_chat
    calls = []

    def add_chat(*args, **kwargs):
        calls.append(args)
        if len(calls) == fail_on_call:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(answer.repo, "add_chat", add_chat)


def test_question_save_failure_yields_error_without_calling_model(
    conn, settings, seeded_video, no_live_frames, monkeypatch
):
    llm = FakeLLM(chat_responses=["never used"], embed_dim=settings.embed_dim)
    failing_add_chat(monkeypatch, 1)

    events = list(stream_answer(conn, llm, settings, seeded_video, "q", 110.0))

    assert events == [{"type": "error", "message": "保存提问失败，请重试"}]
    assert not [c for c in llm.calls if c[0] == "chat_stream"]


def test_partial_save_failure_still_yields_error(conn, settings, seeded_video, no_live_frames, monkeypatch):
    llm = FakeLLM(chat_responses=[["Hel", RuntimeError("cut")]], embed_dim=settings.embed_dim)
    failing_add_chat(monkeypatch, 2)

    events = list(stream_answer(conn, llm, settings, seeded_video, "q", 110.0))

    assert [e["type"] for e in events] == ["delta", "error"]
    assert "chat_id" not in events[-1]


def test_disconnect_with_failing_save_still_closes(conn, settings, seeded_video, no_live_frames, monkeypatch):
    llm = FakeLLM(chat_responses=[["first ", "second"]], embed_dim=settings.embed_dim)
    failing_add_chat(monkeypatch, 2)
    stream = stream_answer(conn, llm, settings, seeded_video, "q", 110.0)

    next(stream)
    stream.close()  # must not raise


def test_transcription_in_progress_is_passed_to_the_context(conn, fake_llm, settings, seeded_video, no_live_frames):
    states = repo.get_video(conn, seeded_video).stage_states
    repo.update_video(conn, seeded_video, transcribed_until=90.0, stage_states={**states, "transcript": "running"})
    assert "still being transcribed" in user_content(prepare_messages(conn, fake_llm, settings, seeded_video, "Q", 110.0))
    repo.update_video(conn, seeded_video, stage_states={**states, "transcript": "failed"})
    assert "still being transcribed" not in user_content(prepare_messages(conn, fake_llm, settings, seeded_video, "Q", 110.0))
    repo.update_video(conn, seeded_video, stage_states={**states, "transcript": "done"})
    assert "still being transcribed" not in user_content(prepare_messages(conn, fake_llm, settings, seeded_video, "Q", 110.0))
