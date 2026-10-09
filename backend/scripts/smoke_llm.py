"""Manual check that the configured real models respond.

Usage: cd backend && uv run python scripts/smoke_llm.py
Needs backend/.env with LLM_API_KEY. Downloads bge-m3 on first run.
"""

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from unstuck.config import load_settings
from unstuck.llm.factory import build_llm


def main() -> None:
    settings = load_settings()
    llm = build_llm(settings)

    print("chat:", llm.chat([{"role": "user", "content": "用一句话解释什么是导数。"}]))
    print("stream:", "".join(llm.chat_stream([{"role": "user", "content": "Say hi in 3 words."}])))

    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "slide.png"
        canvas = Image.new("RGB", (640, 360), "white")
        ImageDraw.Draw(canvas).text((40, 150), "E = m c^2   and   for i in range(3): print(i)", fill="black")
        canvas.save(img)
        print("vision:", llm.vision("Transcribe all text in this image.", img))

    vec = llm.embed(["hello world"])[0]
    print("embed dim:", len(vec), "(expected", settings.embed_dim, ")")


if __name__ == "__main__":
    main()
