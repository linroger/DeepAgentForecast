"""Zep-ontology-compatible base classes backed by Graphiti's Pydantic typing.

``graph_builder.set_ontology`` dynamically builds entity/edge type classes via
``type(name, (EntityModel,), attrs)`` with ``Optional[EntityText]`` / ``Optional[str]``
annotations and ``Field(description=..., default=None)`` values. In Zep these bases came
from ``zep_cloud.external_clients.ontology``. Graphiti accepts plain ``pydantic.BaseModel``
subclasses as ``entity_types`` / ``edge_types``, so we expose drop-in equivalents:

- ``EntityModel`` / ``EdgeModel`` -> ``pydantic.BaseModel`` subclasses (custom attributes
  become optional fields the LLM fills during extraction).
- ``EntityText`` -> ``str`` (a free-text attribute), valid inside ``Optional[...]``.

This keeps the existing dynamic-class construction in graph_builder unchanged while
producing models Graphiti can consume directly.
"""

from __future__ import annotations

from pydantic import BaseModel


class EntityModel(BaseModel):
    """Base class for dynamically-created entity types (Graphiti ``entity_types``).

    Extra fields are IGNORED (the pydantic default). This is deliberate: Graphiti spreads
    each entity attribute as an individual graph-DB property, and FalkorDB only accepts
    primitive (or array-of-primitive) values. Allowing arbitrary extra fields would let the
    LLM inject nested/object values that FalkorDB rejects ("Property values can only be of
    primitive types"). Constraining to the declared ``Optional[str]`` ontology attributes
    keeps every stored value primitive — matching Zep's single-text ``EntityText`` semantics.
    """

    model_config = {"extra": "ignore"}


class EdgeModel(BaseModel):
    """Base class for dynamically-created edge types (Graphiti ``edge_types``).

    Extra fields ignored for the same FalkorDB primitive-only-property reason as EntityModel.
    """

    model_config = {"extra": "ignore"}


# A free-text entity attribute. Graphiti needs a concrete field type; plain ``str``
# is the faithful local equivalent of Zep's ``EntityText``.
EntityText = str
