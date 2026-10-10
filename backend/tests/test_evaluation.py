import json

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
