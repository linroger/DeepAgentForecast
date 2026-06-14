"""Local Graphiti backend exposed behind a Zep-compatible facade.

This package replaces the Zep Cloud SDK (``zep_cloud``) with a locally-running Graphiti
knowledge graph so the app needs no ``ZEP_API_KEY`` and no external/paid service.

Import-path migration map (what each former ``zep_cloud`` import becomes):
  from zep_cloud.client import Zep
      -> from app.services.graphiti_client import Zep
  from zep_cloud import EpisodeData, EntityEdgeSourceTarget, InternalServerError
      -> from app.services.graphiti_client import EpisodeData, EntityEdgeSourceTarget, InternalServerError
  from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel
      -> from app.services.graphiti_client.ontology import EntityModel, EntityText, EdgeModel
  from zep_cloud.core.api_error import ApiError
      -> from app.services.graphiti_client import ApiError
"""

import os as _os

# graphiti_core.embedder.client reads EMBEDDING_DIM ONCE at import and freezes it. The local
# embedder defaults to a 384-dim multilingual model; set the env BEFORE any graphiti import
# so the vector dimension matches. Honors GRAPHITI_EMBED_DIM if the user picks another model.
_os.environ["EMBEDDING_DIM"] = _os.environ.get("GRAPHITI_EMBED_DIM", "384")

from .client import Zep  # noqa: E402
from .compat import ApiError, EntityEdgeSourceTarget, EpisodeData, InternalServerError  # noqa: E402

__all__ = [
    "Zep",
    "EpisodeData",
    "EntityEdgeSourceTarget",
    "InternalServerError",
    "ApiError",
]
