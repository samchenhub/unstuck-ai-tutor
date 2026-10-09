import threading


class BgeM3Embedder:
    """Local BAAI/bge-m3 embeddings via sentence-transformers (loaded lazily, ~2 GB download once)."""

    def __init__(self, model_name: str = "BAAI/bge-m3", batch_size: int = 16):
        self._model_name = model_name
        self._batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self._model_name)
            vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=self._batch_size)
        return [v.tolist() for v in vectors]
