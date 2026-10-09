import shutil
import subprocess

import pytest

from unstuck.config import Settings
from unstuck.db import connect, repo
from unstuck.ingest import indexer
from unstuck.llm import FakeLLM
from unstuck.models import FrameDescription, NewKeyframe, Segment


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        llm_base_url="http://fake.invalid",
        llm_api_key="",
        chat_model="fake-chat",
        vision_model="fake-vision",
        embed_model="fake-embed",
        embed_dim=64,
        whisper_model="tiny",
        llm_disable_thinking=False,
    )


@pytest.fixture
def conn(settings):
    c = connect(settings.db_path, settings.embed_dim)
    yield c
    c.close()


@pytest.fixture
def fake_llm(settings):
    return FakeLLM(embed_dim=settings.embed_dim)


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    """A 3-second generated test video; tests using it are skipped without ffmpeg."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "video.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=3:size=320x240:rate=10", "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )
    return out


SEEDED_ID = "abcdefghijk"
SEEDED_TOPICS = [
    "intro to vectors", "dot product definition", "dot product geometry cosine", "cross product",
    "matrix multiplication", "determinant area", "eigenvalues", "eigenvectors", "diagonalization", "summary",
]


@pytest.fixture
def seeded_video(conn, settings, fake_llm):
    repo.insert_video(conn, SEEDED_ID, f"https://www.youtube.com/watch?v={SEEDED_ID}")
    repo.replace_segments(conn, SEEDED_ID, [
        Segment(i * 30.0, (i + 1) * 30.0, f"In this part we discuss {topic}.")
        for i, topic in enumerate(SEEDED_TOPICS)
    ])
    formula, diagram = repo.replace_keyframes(conn, SEEDED_ID, [
        NewKeyframe(100.0, str(settings.data_dir / "f100.jpg"), "0" * 16),
        NewKeyframe(200.0, str(settings.data_dir / "f200.jpg"), "f" * 16),
    ])
    repo.save_frame_description(conn, formula.id, FrameDescription(
        "formula", "Slide showing the dot product formula", "a \\cdot b = |a||b| \\cos\\theta", ""))
    repo.save_frame_description(conn, diagram.id, FrameDescription(
        "diagram", "Parallelogram spanned by two vectors", "det(A)", "new diagram"))
    indexer.build_index(conn, fake_llm, SEEDED_ID)
    repo.update_video(
        conn, SEEDED_ID,
        title="Linear Algebra Basics", duration=300.0, status="ready", ask_ready=True, visual_mode="full",
        outline=[
            {"start": 0.0, "title": "Vectors", "summary": "Vectors and the dot product"},
            {"start": 120.0, "title": "Matrices", "summary": "Matrix operations"},
        ],
        stage_states={name: "done" for name in (
            "metadata", "transcript", "download", "keyframes", "vision", "timeline", "index", "outline")},
    )
    return SEEDED_ID
