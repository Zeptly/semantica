"""Versioned, authenticated projection API (/v1).

Handlers are synchronous so FastAPI runs them in its threadpool; the
underlying Semantica GraphStore / falkordb client is blocking.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status

from ..auth import require_api_key
from ..graph import EndpointNotFound, ProjectionGraph
from ..models import (
    ID_PATTERN,
    EdgeDeleteResult,
    EdgeOut,
    EdgeUpsert,
    EdgeUpsertResult,
    NodeDeleteResult,
    NodeOut,
    NodeUpsert,
    NodeUpsertResult,
    Relationship,
    RelationshipQuery,
    RelationshipQueryResult,
    utc_iso,
)

router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}",
    tags=["projection"],
    dependencies=[Depends(require_api_key)],
)

WorkspaceId = Annotated[str, Path(pattern=ID_PATTERN, description="Zeptly workspace ID")]
NodeId = Annotated[str, Path(pattern=ID_PATTERN, description="Canonical node ID")]
EdgeId = Annotated[str, Path(pattern=ID_PATTERN, description="Canonical edge ID")]

_METADATA_FIELDS = (
    "lifecycle_state",
    "is_current",
    "source_version",
    "provenance_refs",
)
_TIME_FIELDS = ("valid_from", "valid_to", "source_updated_at")


def _graph(request: Request) -> ProjectionGraph:
    return request.app.state.graph


def _mismatch(field: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=f"body {field} does not match the request path",
    )


def _metadata(body: Any) -> Dict[str, Any]:
    props: Dict[str, Any] = {f: getattr(body, f) for f in _METADATA_FIELDS}
    props["provenance_refs"] = list(body.provenance_refs)
    for f in _TIME_FIELDS:
        props[f] = utc_iso(getattr(body, f))
    return props


# -- nodes -----------------------------------------------------------------


@router.put("/nodes/{node_id}", response_model=NodeUpsertResult)
def put_node(
    workspace_id: WorkspaceId,
    node_id: NodeId,
    body: NodeUpsert,
    request: Request,
    response: Response,
) -> NodeUpsertResult:
    if body.workspace_id != workspace_id:
        raise _mismatch("workspace_id")
    if body.canonical_id != node_id:
        raise _mismatch("canonical_id")
    props = _metadata(body)
    props.update(node_type=body.node_type, label=body.label)
    stored, created = _graph(request).upsert_node(workspace_id, node_id, props)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return NodeUpsertResult(created=created, node=NodeOut(**stored))


@router.get("/nodes/{node_id}", response_model=NodeOut)
def get_node(workspace_id: WorkspaceId, node_id: NodeId, request: Request) -> NodeOut:
    stored = _graph(request).get_node(workspace_id, node_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="node not found")
    return NodeOut(**stored)


@router.delete("/nodes/{node_id}", response_model=NodeDeleteResult)
def delete_node(workspace_id: WorkspaceId, node_id: NodeId, request: Request) -> NodeDeleteResult:
    detached = _graph(request).delete_node(workspace_id, node_id)
    if detached is None:
        raise HTTPException(status_code=404, detail="node not found")
    return NodeDeleteResult(
        workspace_id=workspace_id,
        canonical_id=node_id,
        deleted=True,
        detached_edges=detached,
    )


# -- edges -----------------------------------------------------------------


@router.put("/edges/{edge_id}", response_model=EdgeUpsertResult)
def put_edge(
    workspace_id: WorkspaceId,
    edge_id: EdgeId,
    body: EdgeUpsert,
    request: Request,
    response: Response,
) -> EdgeUpsertResult:
    if body.workspace_id != workspace_id:
        raise _mismatch("workspace_id")
    if body.edge_id != edge_id:
        raise _mismatch("edge_id")
    props = _metadata(body)
    props["edge_type"] = body.edge_type
    try:
        stored, created = _graph(request).upsert_edge(
            workspace_id, edge_id, body.source_node_id, body.target_node_id, props
        )
    except EndpointNotFound:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="source or target node does not exist in this workspace",
        ) from None
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return EdgeUpsertResult(created=created, edge=EdgeOut(**stored))


@router.delete("/edges/{edge_id}", response_model=EdgeDeleteResult)
def delete_edge(workspace_id: WorkspaceId, edge_id: EdgeId, request: Request) -> EdgeDeleteResult:
    if not _graph(request).delete_edge(workspace_id, edge_id):
        raise HTTPException(status_code=404, detail="edge not found")
    return EdgeDeleteResult(workspace_id=workspace_id, edge_id=edge_id, deleted=True)


# -- relationship query ------------------------------------------------------


@router.post("/relationships/query", response_model=RelationshipQueryResult)
def query_relationships(
    workspace_id: WorkspaceId, body: RelationshipQuery, request: Request
) -> RelationshipQueryResult:
    if body.workspace_id != workspace_id:
        raise _mismatch("workspace_id")
    anchor, rows, truncated = _graph(request).relationships(
        workspace_id,
        body.node_id,
        body.direction,
        list(body.edge_types) if body.edge_types else None,
        body.include_non_current,
        body.limit,
    )
    if anchor is None:
        raise HTTPException(status_code=404, detail="node not found")
    return RelationshipQueryResult(
        workspace_id=workspace_id,
        node=NodeOut(**anchor),
        relationships=[
            Relationship(
                direction=r.direction,  # type: ignore[arg-type]
                edge=EdgeOut(**r.edge),
                neighbor=NodeOut(**r.neighbor),
            )
            for r in rows
        ],
        truncated=truncated,
    )
