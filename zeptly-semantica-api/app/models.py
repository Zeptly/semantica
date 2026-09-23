"""Strict request/response models.

Payload policy: a projection node carries only identity, type, workspace,
lifecycle/currentness, temporal metadata, provenance references and a short
human-readable label. Every request model forbids unknown fields, so content,
prompts, model inputs/outputs, embeddings, credentials, documents and any
other unlisted field are rejected with 422.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, List, Literal, Optional

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    model_validator,
)

LABEL_MAX_LENGTH = 160
MAX_PROVENANCE_REFS = 16
MAX_QUERY_EDGE_TYPES = 16
MAX_QUERY_LIMIT = 200

# Identifiers never contain ':' so the internal "{workspace_id}:{id}" key is
# unambiguous (no two (workspace, id) pairs can produce the same key).
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$"
NODE_TYPE_PATTERN = r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$"
EDGE_TYPE_PATTERN = r"^[A-Z][A-Z0-9_]{0,63}$"
LIFECYCLE_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
PROVENANCE_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/#@=\-]{0,255}$"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

Identifier = Annotated[str, StringConstraints(strict=True, pattern=ID_PATTERN)]
NodeType = Annotated[str, StringConstraints(strict=True, pattern=NODE_TYPE_PATTERN)]
EdgeType = Annotated[str, StringConstraints(strict=True, pattern=EDGE_TYPE_PATTERN)]
LifecycleState = Annotated[str, StringConstraints(strict=True, pattern=LIFECYCLE_PATTERN)]
ProvenanceRef = Annotated[str, StringConstraints(strict=True, pattern=PROVENANCE_REF_PATTERN)]


def _check_label(value: str) -> str:
    if _CONTROL_CHARS.search(value):
        raise ValueError("label must not contain control characters")
    if not value.strip():
        raise ValueError("label must not be blank")
    return value


Label = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=LABEL_MAX_LENGTH),
    AfterValidator(_check_label),
]


def _unique(values: Optional[List[str]]) -> Optional[List[str]]:
    if values is not None and len(set(values)) != len(values):
        raise ValueError("values must be unique")
    return values


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ProjectionMetadata(_Strict):
    """Lifecycle, temporal and provenance metadata shared by nodes and edges."""

    lifecycle_state: Optional[LifecycleState] = Field(
        default=None, description="Canonical lifecycle state mirrored from PostgreSQL."
    )
    is_current: StrictBool = Field(
        default=True, description="Currentness flag mirrored from PostgreSQL."
    )
    valid_from: Optional[AwareDatetime] = None
    valid_to: Optional[AwareDatetime] = None
    source_updated_at: Optional[AwareDatetime] = Field(
        default=None, description="Timestamp of the canonical row this projects."
    )
    source_version: Optional[StrictInt] = Field(
        default=None, ge=0, description="Canonical row version this projects."
    )
    provenance_refs: Annotated[
        List[ProvenanceRef],
        Field(max_length=MAX_PROVENANCE_REFS),
        AfterValidator(_unique),
    ] = Field(default_factory=list, description="References to canonical sources.")

    @model_validator(mode="after")
    def _check_interval(self):  # type: ignore[no-untyped-def]
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be earlier than valid_from")
        return self


class NodeUpsert(_ProjectionMetadata):
    workspace_id: Identifier
    canonical_id: Identifier
    node_type: NodeType
    label: Optional[Label] = None


class EdgeUpsert(_ProjectionMetadata):
    workspace_id: Identifier
    edge_id: Identifier
    edge_type: EdgeType
    source_node_id: Identifier
    target_node_id: Identifier


class NodeOut(BaseModel):
    workspace_id: str
    canonical_id: str
    node_type: str
    label: Optional[str] = None
    lifecycle_state: Optional[str] = None
    is_current: bool
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    source_updated_at: Optional[datetime] = None
    source_version: Optional[int] = None
    provenance_refs: List[str] = Field(default_factory=list)
    projected_at: datetime = Field(description="First time this projection was written.")
    updated_at: datetime = Field(description="Last time this projection was written.")


class EdgeOut(BaseModel):
    workspace_id: str
    edge_id: str
    edge_type: str
    source_node_id: str
    target_node_id: str
    lifecycle_state: Optional[str] = None
    is_current: bool
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    source_updated_at: Optional[datetime] = None
    source_version: Optional[int] = None
    provenance_refs: List[str] = Field(default_factory=list)
    projected_at: datetime
    updated_at: datetime


class NodeUpsertResult(BaseModel):
    created: bool
    node: NodeOut


class EdgeUpsertResult(BaseModel):
    created: bool
    edge: EdgeOut


class NodeDeleteResult(BaseModel):
    workspace_id: str
    canonical_id: str
    deleted: bool
    detached_edges: int = Field(
        description="Projection edges removed because they were connected to the node."
    )


class EdgeDeleteResult(BaseModel):
    workspace_id: str
    edge_id: str
    deleted: bool


class RelationshipQuery(_Strict):
    """The only query shape the service accepts: one-hop explicit relationships
    of a single anchor node, inside a single workspace."""

    workspace_id: Identifier
    node_id: Identifier
    direction: Literal["outgoing", "incoming", "both"] = "both"
    edge_types: Optional[
        Annotated[
            List[EdgeType],
            Field(min_length=1, max_length=MAX_QUERY_EDGE_TYPES),
            AfterValidator(_unique),
        ]
    ] = None
    include_non_current: StrictBool = False
    limit: StrictInt = Field(default=50, ge=1, le=MAX_QUERY_LIMIT)


class Relationship(BaseModel):
    direction: Literal["outgoing", "incoming"]
    edge: EdgeOut
    neighbor: NodeOut


class RelationshipQueryResult(BaseModel):
    workspace_id: str
    node: NodeOut
    relationships: List[Relationship]
    truncated: bool


def utc_iso(value: Optional[datetime]) -> Optional[str]:
    """Normalise an aware datetime to an ISO-8601 UTC string for storage."""
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()
