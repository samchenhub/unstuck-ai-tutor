import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    llm_base_url: str
    llm_api_key: str
    chat_model: str
    vision_model: str
    embed_model: str
    embed_dim: int
    whisper_model: str
    max_duration_s: int = 7200
    llm_disable_thinking: bool = True
    llm_max_concurrency: int = 2
    bilibili_cookies_browser: str = "chrome"
    groq_api_key: str = ""  # cloud speech-to-text; empty = transcribe locally with WHISPER_MODEL
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_whisper_model: str = "whisper-large-v3-turbo"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "unstuck.db"

    @property
    def bilibili_cookie_file(self) -> Path:
        """Where the QR-code login keeps its cookies."""
        return self.data_dir / "bilibili_cookies.txt"

    def video_dir(self, video_id: str) -> Path:
        return self.data_dir / "videos" / video_id


def _flag(value: str) -> bool:
    return value.strip().lower() not in ("0", "false", "no", "")


def load_settings(env_file: bool = True) -> Settings:
    if env_file:
        load_dotenv()
    return Settings(
        data_dir=Path(os.getenv("UNSTUCK_DATA_DIR", "data")).expanduser().absolute(),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        chat_model=os.getenv("CHAT_MODEL", "glm-4.7-flash"),
        vision_model=os.getenv("VISION_MODEL", "glm-4.6v-flash"),
        embed_model=os.getenv("EMBED_MODEL", "BAAI/bge-m3"),
        embed_dim=int(os.getenv("EMBED_DIM", "1024")),
        whisper_model=os.getenv("WHISPER_MODEL", "small"),
        max_duration_s=int(os.getenv("MAX_DURATION_S", "7200")),
        llm_disable_thinking=_flag(os.getenv("LLM_DISABLE_THINKING", "1")),
        llm_max_concurrency=int(os.getenv("LLM_MAX_CONCURRENCY", "2")),
        # Chrome's cookies are unreadable to other programs on Windows (app-bound encryption): use the QR login.
        bilibili_cookies_browser=os.getenv("BILIBILI_COOKIES_BROWSER", "" if os.name == "nt" else "chrome").strip(),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        groq_whisper_model=os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo"),
    )
