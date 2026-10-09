from unstuck.config import Settings
from unstuck.llm.base import LLM
from unstuck.llm.embedder import BgeM3Embedder
from unstuck.llm.openai_compat import OpenAICompatLLM


def build_llm(settings: Settings) -> LLM:
    extra_body = {"thinking": {"type": "disabled"}} if settings.llm_disable_thinking else None
    return OpenAICompatLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        chat_model=settings.chat_model,
        vision_model=settings.vision_model,
        embedder=BgeM3Embedder(settings.embed_model),
        extra_body=extra_body,
        max_concurrency=settings.llm_max_concurrency,
    )
