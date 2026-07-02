"""Local cross-encoder reranker for Graphiti.

Graphiti's default ``cross_encoder`` is ``OpenAIRerankerClient`` which requires an OpenAI
API key — exactly what this migration removes. Our search facade uses RRF-based recipes
(reciprocal rank fusion) which do NOT invoke a cross-encoder, so a no-op passthrough is
sufficient to satisfy ``Graphiti(...)`` construction without any model download or key.

If higher-quality reranking is desired, set ``GRAPHITI_RERANKER=bge`` to use a fully-local
``BGERerankerClient`` (sentence-transformers); the facade then uses cross-encoder recipes.
"""

from __future__ import annotations

from graphiti_core.cross_encoder.client import CrossEncoderClient


class NoOpCrossEncoder(CrossEncoderClient):
    """Passthrough reranker: returns passages with descending placeholder scores in their
    original order. Used only to satisfy ``Graphiti(...)`` construction. NOTE (KG-1): since
    I-1-0 mapped reranker→recipe, cross-encoder recipes CAN reach ``rank``; the runtime
    search facade therefore degrades cross_encoder recipes to their RRF twins whenever this
    no-op is the active reranker (a no-op rank skips both RRF fusion and semantic rerank)."""

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        n = len(passages)
        return [(p, float(n - i)) for i, p in enumerate(passages)]
