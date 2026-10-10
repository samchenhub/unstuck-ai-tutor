"""Compare transcript-only vs transcript+frames answers on a hand-written question set.

Usage (videos must already be imported through the app, status "ready"):
  cd backend && uv run python ../eval/run_eval.py ../eval/questions.json
"""

import argparse
import datetime
import sys
from pathlib import Path

from unstuck.config import load_settings
from unstuck.db import connect, repo
from unstuck.evaluation import load_items, render_report, run_eval, summarize
from unstuck.llm.factory import build_llm

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    settings = load_settings()
    conn = connect(settings.db_path, settings.embed_dim)
    items = load_items(args.questions)

    not_ready = sorted({
        item.video_id for item in items
        if (video := repo.get_video(conn, item.video_id)) is None or video.status != "ready"
    })
    if not_ready:
        print(f"Import these videos first (status must be 'ready'): {', '.join(not_ready)}", file=sys.stderr)
        return 1

    results = run_eval(conn, build_llm(settings), settings, items)
    out = args.out or RESULTS_DIR / f"report-{datetime.date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(results, settings.chat_model), encoding="utf-8")
    for mode, score in summarize(results).items():
        print(f"{mode}: {score:.0%}")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
