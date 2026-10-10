from unstuck.config import load_settings

ENV_KEYS = (
    "LLM_BASE_URL", "LLM_API_KEY", "CHAT_MODEL", "VISION_MODEL", "EMBED_MODEL",
    "EMBED_DIM", "WHISPER_MODEL", "MAX_DURATION_S", "LLM_DISABLE_THINKING",
    "LLM_MAX_CONCURRENCY",
)


def test_defaults(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("UNSTUCK_DATA_DIR", str(tmp_path))

    s = load_settings(env_file=False)

    assert s.chat_model == "glm-4.7-flash"
    assert s.vision_model == "glm-4.6v-flash"
    assert s.llm_base_url == "https://open.bigmodel.cn/api/paas/v4/"
    assert s.embed_model == "BAAI/bge-m3"
    assert s.embed_dim == 1024
    assert s.max_duration_s == 7200
    assert s.llm_disable_thinking is True
    assert s.db_path == tmp_path / "unstuck.db"
    assert s.video_dir("abc") == tmp_path / "videos" / "abc"


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("UNSTUCK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHAT_MODEL", "claude-x")
    monkeypatch.setenv("EMBED_DIM", "8")
    monkeypatch.setenv("LLM_DISABLE_THINKING", "0")

    s = load_settings(env_file=False)

    assert s.chat_model == "claude-x"
    assert s.embed_dim == 8
    assert s.llm_disable_thinking is False


def test_bilibili_cookie_browser_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("UNSTUCK_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BILIBILI_COOKIES_BROWSER", raising=False)
    assert load_settings(env_file=False).bilibili_cookies_browser == "chrome"
    monkeypatch.setenv("BILIBILI_COOKIES_BROWSER", "")
    assert load_settings(env_file=False).bilibili_cookies_browser == ""
