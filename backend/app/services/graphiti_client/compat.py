"""Zep-SDK-compatible symbols backed by local Graphiti.

The codebase historically imported a handful of value/exception types directly from
the ``zep_cloud`` package. To keep the migration to a local Graphiti knowledge graph
a drop-in replacement, we re-expose the same names with the same shapes so call sites
need only change their import path (``zep_cloud`` -> ``app.services.graphiti_client``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class EpisodeData:
    """Mirror of ``zep_cloud.EpisodeData``.

    The app builds these as ``EpisodeData(data=<chunk>, type="text")`` and hands a list
    to ``client.graph.add_batch``. ``reference_time`` (EXECPLAN T2.3) optionally stamps
    the chunk's bi-temporal validity (defaults to ingest time when absent).
    """

    data: str
    type: str = "text"
    reference_time: Optional[datetime] = None


@dataclass
class EntityEdgeSourceTarget:
    """Mirror of ``zep_cloud.EntityEdgeSourceTarget`` used to declare which entity
    types an edge may connect (source -> target)."""

    source: Optional[str] = None
    target: Optional[str] = None


class ApiError(Exception):
    """Mirror of ``zep_cloud.core.api_error.ApiError``.

    Carries an optional ``status_code`` so the demo-export 404-rebuild branch keeps
    type-checking. A local graph never 404s, so this is effectively dead but importable.
    """

    def __init__(self, message: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class InternalServerError(Exception):
    """Mirror of ``zep_cloud.InternalServerError``.

    The paging retry loop treats this as a transient, retryable error. Against a local
    graph DB it is rarely raised, but the symbol must stay importable.
    """
