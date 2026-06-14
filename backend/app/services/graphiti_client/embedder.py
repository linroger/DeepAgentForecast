"""Fully-local embedder for Graphiti using sentence-transformers.

Zep Cloud computed embeddings server-side; locally we must supply an embedder. The app's
LLM provider may not expose an embeddings endpoint (e.g. minimax/kimi/claude-cli), so we
default to a local sentence-transformers model — no API key, provider-independent, and
multilingual (the app handles bilingual EN/中文 content).

Model + dimension are configurable via env:
  GRAPHITI_EMBED_MODEL  (default: paraphrase-multilingual-MiniLM-L12-v2, 384-dim)
  GRAPHITI_EMBED_DIM    (must match the model; also exported as EMBEDDING_DIM)

NOTE: ``EMBEDDING_DIM`` is read once, frozen at import, inside graphiti_core.embedder.client.
The package ``__init__`` sets it BEFORE importing graphiti, so this module's import is safe.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Iterable

from graphiti_core.embedder.client import EmbedderClient

DEFAULT_EMBED_MODEL = os.environ.get(
    "GRAPHITI_EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)
DEFAULT_EMBED_DIM = int(os.environ.get("GRAPHITI_EMBED_DIM", "384"))


class LocalSentenceTransformerEmbedder(EmbedderClient):
    """Embeds text locally with a sentence-transformers model.

    The model is loaded lazily on first use (so importing the package — e.g. for the
    Flask app boot — does not pay the model-load cost until a graph is actually built or
    searched). Encoding is offloaded to a thread so the async event loop stays responsive.
    """

    def __init__(self, model_name: str | None = None, embedding_dim: int | None = None):
        self.model_name = model_name or DEFAULT_EMBED_MODEL
        self.embedding_dim = embedding_dim or DEFAULT_EMBED_DIM
        self._model = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    # hf_transfer is an optional download accelerator; if the env opts into
                    # it (HF_HUB_ENABLE_HF_TRANSFER=1) but the package isn't installed, HF
                    # downloads hard-fail. Disable it when unavailable so the standard
                    # download path works on a fresh machine.
                    import importlib.util

                    if importlib.util.find_spec("hf_transfer") is None:
                        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"

                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(self.model_name)
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        # normalize_embeddings=True -> unit vectors, matching Graphiti's cosine search
        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        out: list[list[float]] = []
        for row in embeddings:
            vec = [float(x) for x in row][: self.embedding_dim]
            out.append(vec)
        return out

    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        # Mirror OpenAIEmbedder.create: return the embedding of the first input.
        if isinstance(input_data, str):
            texts = [input_data]
        else:
            texts = [str(x) for x in input_data]  # type: ignore[arg-type]
        if not texts:
            return [0.0] * self.embedding_dim
        loop = asyncio.get_event_loop()
        vecs = await loop.run_in_executor(None, self._encode, texts)
        return vecs[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        if not input_data_list:
            return []
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._encode, list(input_data_list))
