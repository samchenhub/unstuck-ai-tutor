import shutil
import subprocess

import pytest

from unstuck.config import Settings
from unstuck.db import connect
from unstuck.llm import FakeLLM


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
