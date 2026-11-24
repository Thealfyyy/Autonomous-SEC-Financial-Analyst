"""Embedding interface and local SentenceTransformer implementation (BAAI/bge-m3)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Embedder(Protocol):
    """Protocol for dense text embedding providers."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return L2-normalized embedding vectors for each input string."""
        ...


class SentenceTransformerEmbedder:
    """Lazy-loading wrapper around SentenceTransformer (defaults to local .cache)."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        cache_folder: Path | str = Path(".cache/sentence_transformers"),
        device: str | None = None,
        batch_size: int = 16,
    ) -> None:
        self.model_name = model_name
        self.cache_folder = str(cache_folder)
        self.device = device
        self.batch_size = batch_size
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self.model_name,
                cache_folder=self.cache_folder,
                device=self.device,
            )
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        vectors = model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vec.tolist() for vec in vectors]
