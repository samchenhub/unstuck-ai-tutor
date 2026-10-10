import json

import pytest

from unstuck.db import repo
from unstuck.evaluation import (
    EvalItem,
    ItemResult,
    load_items,
    parse_judgement,
    render_report,
    run_eval,
    summarize,
)
from unstuck.llm import FakeLLM


def item(id="q1", key_points=("uses cosine", "mentions angle")):
    return EvalItem(id=id, video_id="abcdefghijk", t=110.0, question="Why cosine?", key_points=list(key_points))


def test_load_items_accepts_mmss(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([
        {"id": "a", "video_id": "abcdefghijk", "t": "01:50", "question": "q", "key_points": ["k"]},
        {"id": "b", "video_id": "abcdefghijk", "t": 5, "question": "q2", "key_points": ["k1", "k2"]},
    ]))
    items = load_items(path)
    assert [(i.id, i.t) for i in items] == [("a", 110.0), ("b", 5.0)]
    assert items[1].key_points == ["k1", "k2"]


def test_parse_judgement_variants():
    assert parse_judgement('{"covered": [true, false]}', 2) == [True, False]
    assert parse_judgement('```json\n{"covered": ["yes", "no"]}\n```', 2) == [True, False]
    assert parse_judgement('{"covered": [true]}', 3) == [True, False, False]
    assert parse_judgement('{"covered": [true, true, true]}', 2) == [True, True]
    assert parse_judgement("I think it is fine", 2) == [False, False]


def test_score_and_summary():
    results = [
        ItemResult(item(), "transcript_only", "a", [True, False]),
        ItemResult(item(), "full", "b", [True, True]),
        ItemResult(item("q2"), "transcript_only", "c", [False, False]),
        ItemResult(item("q2"), "full", "d", [True, False]),
    ]
    assert results[0].score == 0.5
    assert summarize(results) == {"transcript_only": 0.25, "full": 0.75}


def test_render_report():
    results = [
        ItemResult(item(), "transcript_only", "answer A", [True, False]),
        ItemResult(item(), "full", "answer B", [True, True]),
    ]
    report = render_report(results, model_name="glm-test")
    assert "glm-test" in report
    assert "| transcript_only | 50% |" in report
    assert "| full | 100% |" in report
    assert "| q1 | 01:50 | Why cosine? | 50% | 100% |" in report
    assert "answer B" in report


def test_run_eval_answers_and_judges_each_mode(conn, settings, seeded_video):
    llm = FakeLLM(
        chat_responses=[
            "transcript answer", '{"covered": [true, false]}',
            "visual answer", '{"covered": [true, true]}',
        ],
        embed_dim=settings.embed_dim,
    )

    results = run_eval(conn, llm, settings, [item()])

    assert [(r.mode, r.answer, r.covered) for r in results] == [
        ("transcript_only", "transcript answer", [True, False]),
        ("full", "visual answer", [True, True]),
    ]
    chat_calls = [args for name, args in llm.calls if name == "chat"]
    assert "Not available" in chat_calls[0][-1]["content"]
    assert "Slide showing the dot product formula" in chat_calls[2][-1]["content"]
    assert "transcript answer" in chat_calls[1][-1]["content"]


def test_run_eval_rejects_full_mode_on_transcript_only_video(conn, settings, seeded_video):
    repo.update_video(conn, seeded_video, visual_mode="transcript_only")
    llm = FakeLLM(embed_dim=settings.embed_dim)

    with pytest.raises(ValueError, match="abcdefghijk"):
        run_eval(conn, llm, settings, [item()])
    assert llm.calls == []

    results = run_eval(conn, llm, settings, [item()], modes=("transcript_only",))
    assert [r.mode for r in results] == ["transcript_only"]


def test_render_report_escapes_table_text():
    tricky = EvalItem(id="a|b", video_id="abcdefghijk", t=110.0, question="why is |A| zero?\nreally", key_points=["k"])
    results = [ItemResult(tricky, "transcript_only", "x", [True]), ItemResult(tricky, "full", "y", [False])]

    report = render_report(results, model_name="m")

    row = next(line for line in report.splitlines() if "why is" in line and line.startswith("| a"))
    assert row == r"| a\|b | 01:50 | why is \|A\| zero? really | 100% | 0% |"
    heading = next(line for line in report.splitlines() if line.startswith("### "))
    assert "\n" not in heading and "why is \\|A\\| zero? really" in heading


def test_run_eval_survives_a_failing_item_and_unparsed_judge(conn, settings, seeded_video):
    llm = FakeLLM(
        chat_responses=[
            RuntimeError("boom"),
            "visual answer", "not json at all",
        ],
        embed_dim=settings.embed_dim,
    )

    results = run_eval(conn, llm, settings, [item()])

    first, second = results
    assert first.failed and first.answer == "(error: boom)" and first.covered == [False, False]
    assert not first.judge_unparsed
    assert not second.failed and second.answer == "visual answer"
    assert second.judge_unparsed and second.covered == [False, False]

    report = render_report(results, model_name="m")
    assert "⚠" in report
    assert "(error: boom)" in report
    assert "judge" in report.lower()


def test_load_items_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "q.json"
    entry = {"id": "a", "video_id": "abcdefghijk", "t": 1, "question": "q", "key_points": ["k"]}
    path.write_text(json.dumps([entry, entry]))
    with pytest.raises(ValueError, match="duplicate id.*a"):
        load_items(path)


def test_load_items_rejects_empty_key_points_and_missing_fields(tmp_path):
    path = tmp_path / "q.json"
    ok = {"id": "a", "video_id": "abcdefghijk", "t": 1, "question": "q", "key_points": ["k"]}
    path.write_text(json.dumps([ok, {**ok, "id": "b", "key_points": []}]))
    with pytest.raises(ValueError, match="item 1.*key_points"):
        load_items(path)

    path.write_text(json.dumps([ok, {"id": "c", "video_id": "abcdefghijk", "t": 1, "key_points": ["k"]}]))
    with pytest.raises(ValueError, match="item 1.*question"):
        load_items(path)
