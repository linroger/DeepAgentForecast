"""FalkorDB driver hardened against non-primitive property values.

FalkorDB only accepts node/edge property values that are primitives (string/number/bool)
or arrays of primitives. Graphiti spreads each extracted entity/edge ATTRIBUTE as an
individual graph property, and an LLM occasionally emits a structured value (a nested
object, or a list of objects) for an attribute. Upstream Graphiti passes that straight
through, so a single such extraction aborts the whole graph build with:

    redis.exceptions.ResponseError: Property values can only be of primitive types
    or arrays of primitive types

We can't predict LLM output, so we make the write path bulletproof: this driver coerces
any non-primitive property value to a compact JSON string just before the Cypher runs.
The value is still stored and queryable (as text) instead of crashing ingestion. Primitive
values, arrays of primitives (embeddings, labels, episode-id lists), and datetimes (handled
by the base driver) pass through untouched.
"""

from __future__ import annotations

import json
from typing import Any

from graphiti_core.driver.driver import GraphDriver, GraphProvider
from graphiti_core.driver.falkordb_driver import FalkorDriver, FalkorDriverSession


def _is_primitive(v: Any) -> bool:
    # bool is a subclass of int; both are primitives here. None is allowed.
    return v is None or isinstance(v, (str, int, float, bool))


def _coerce_property_value(v: Any) -> Any:
    """Coerce a single property value to something FalkorDB accepts."""
    if _is_primitive(v):
        return v
    if isinstance(v, (list, tuple)):
        if all(_is_primitive(x) for x in v):
            return list(v)
        # list containing objects/nested lists -> JSON string
        return json.dumps(list(v), ensure_ascii=False, default=str)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, default=str)
    # datetimes and other objects: leave for the base driver's
    # convert_datetimes_to_strings; anything else is stringified defensively.
    import datetime as _dt

    if isinstance(v, (_dt.datetime, _dt.date)):
        return v
    return str(v)


def _sanitize_row(row: dict) -> dict:
    return {k: _coerce_property_value(v) for k, v in row.items()}


def _sanitize_params(kwargs: dict) -> dict:
    """Sanitize Cypher params. UNWIND inputs arrive as a list of row-dicts (each row's
    values become properties); single-row params arrive as a dict. Scalar/array params
    (e.g. group_ids, a uuid) pass through."""
    out: dict = {}
    for key, value in kwargs.items():
        if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
            out[key] = [_sanitize_row(r) for r in value]
        elif isinstance(value, dict):
            out[key] = _sanitize_row(value)
        else:
            out[key] = value
    return out


class SanitizingFalkorSession(FalkorDriverSession):
    async def run(self, query, **kwargs: Any) -> Any:  # type: ignore[override]
        if isinstance(query, list):
            # Batched form: list of (cypher, params). params may carry UNWIND row-lists
            # (e.g. {'nodes': [...]}) or a single-row map, so descend with _sanitize_params
            # (NOT _sanitize_row, which would stringify the row-list/map itself).
            sanitized = [(cypher, _sanitize_params(params or {})) for cypher, params in query]
            return await super().run(sanitized, **kwargs)
        return await super().run(query, **_sanitize_params(kwargs))


class SanitizingFalkorDriver(FalkorDriver):
    """FalkorDriver that JSON-encodes non-primitive property values on every write."""

    def session(self, database: str | None = None):  # type: ignore[override]
        return SanitizingFalkorSession(self._get_graph(database))

    async def execute_query(self, cypher_query_, **kwargs: Any):
        return await super().execute_query(cypher_query_, **_sanitize_params(kwargs))

    def clone(self, database: str) -> GraphDriver:
        if database == self._database:
            return self
        if database == self.default_group_id:
            return SanitizingFalkorDriver(falkor_db=self.client)
        return SanitizingFalkorDriver(falkor_db=self.client, database=database)

    provider = GraphProvider.FALKORDB
