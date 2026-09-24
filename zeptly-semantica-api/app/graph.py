"""Workspace-scoped semantic projection store on Semantica GraphStore + FalkorDB.

Every graph operation goes through ``semantica.graph_store.GraphStore`` (pinned
to semantica==0.6.0) with the ``falkordb`` backend. All Cypher is a fixed
template in this module; client input only ever reaches the database as a
query *parameter*, never as query text.

Graph model (single FalkorDB graph, default name ``zeptly_semantica``):

    (:SemanticaNode {key, workspace_id, canonical_id, node_type, ...})
        -[:SEMANTIC_EDGE {key, workspace_id, edge_id, edge_type, ...}]->
    (:SemanticaNode ...)

* ``key`` is ``"{workspace_id}:{id}"``. Identifiers cannot contain ':' (see
  models.ID_PATTERN) so keys are unambiguous. Keys are derived here from the
  path's workspace id; callers never supply them.
* Every query additionally filters on ``workspace_id`` for the anchor node,
  every traversed edge and every neighbour, so data from another workspace is
  unreachable even if a cross-workspace edge were ever written by other means.
* The client-facing edge type is stored as a property, not as the Cypher
  relationship type, so it never has to be interpolated into query text.

This state is a derived projection of canonical PostgreSQL state in Zeptly. It
holds no authority and can be dropped and rebuilt at any time.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

from semantica.graph_store import GraphStore
from semantica.graph_store.falkordb_store import FalkorDBClient
from semantica.utils.progress_tracker import get_progress_tracker

from .config import Settings
from .models import ID_PATTERN

logger = logging.getLogger("zeptly_semantica.graph")

NODE_LABEL = "SemanticaNode"
EDGE_TYPE = "SEMANTIC_EDGE"

_ID_RE = re.compile(ID_PATTERN)

# Properties maintained by the service itself and never returned verbatim.
_INTERNAL_PROPS = ("key",)


class GraphUnavailable(RuntimeError):
    """The graph store cannot be reached."""


class GraphError(RuntimeError):
    """The graph store rejected or failed an operation."""


class EndpointNotFound(LookupError):
    """An edge endpoint does not exist in the requested workspace."""


def scoped_key(workspace_id: str, local_id: str) -> str:
    """Deterministic workspace-scoped identity. Re-validates both parts so the
    graph layer never relies on the caller having validated them."""
    if not _ID_RE.fullmatch(workspace_id) or not _ID_RE.fullmatch(local_id):
        raise ValueError("invalid identifier")
    return f"{workspace_id}:{local_id}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_connection_failure(exc: BaseException) -> bool:
    """semantica wraps driver errors in ProcessingError; walk the chain to find
    a transport-level failure (connection refused, DNS, timeout, auth loss)."""
    import redis.exceptions as rexc

    seen = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(
            cur,
            (
                rexc.ConnectionError,
                rexc.TimeoutError,
                rexc.AuthenticationError,
                ConnectionError,
                TimeoutError,
                OSError,
            ),
        ):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


# --------------------------------------------------------------------------
# Fixed Cypher templates. Parameters only; no string formatting of input.
# --------------------------------------------------------------------------

_Q_PING = "RETURN 1"

_Q_INDEXES = (
    f"CREATE INDEX FOR (n:{NODE_LABEL}) ON (n.key)",
    f"CREATE INDEX FOR (n:{NODE_LABEL}) ON (n.workspace_id)",
    f"CREATE INDEX FOR ()-[r:{EDGE_TYPE}]-() ON (r.key)",
)

_Q_UPSERT_NODE = f"""
MERGE (n:{NODE_LABEL} {{key: $key}})
WITH n, n.updated_at IS NULL AS created, n.projected_at AS existing
WHERE created OR n.workspace_id = $ws
SET n = $props, n.projected_at = coalesce(existing, $now), n.updated_at = $now
RETURN properties(n), created
"""

_Q_GET_NODE = f"""
MATCH (n:{NODE_LABEL} {{key: $key}})
WHERE n.workspace_id = $ws
RETURN properties(n)
"""

_Q_DELETE_NODE = f"""
MATCH (n:{NODE_LABEL} {{key: $key}})
WHERE n.workspace_id = $ws
OPTIONAL MATCH (n)-[r]-()
WITH n, count(DISTINCT r) AS detached
DETACH DELETE n
RETURN detached
"""

_Q_UPSERT_EDGE = f"""
MATCH (s:{NODE_LABEL} {{key: $src_key}}) WHERE s.workspace_id = $ws
MATCH (t:{NODE_LABEL} {{key: $tgt_key}}) WHERE t.workspace_id = $ws
OPTIONAL MATCH (a:{NODE_LABEL})-[old:{EDGE_TYPE} {{key: $key}}]->(b:{NODE_LABEL})
WHERE old.workspace_id = $ws AND (a.key <> $src_key OR b.key <> $tgt_key)
WITH s, t, collect(old) AS stale, min(old.projected_at) AS moved_from
FOREACH (x IN stale | DELETE x)
MERGE (s)-[r:{EDGE_TYPE} {{key: $key}}]->(t)
WITH r, moved_from, r.updated_at IS NULL AS fresh, r.projected_at AS existing
SET r = $props, r.projected_at = coalesce(existing, moved_from, $now), r.updated_at = $now
RETURN properties(r), fresh AND moved_from IS NULL
"""

_Q_DELETE_EDGE = f"""
MATCH (:{NODE_LABEL})-[r:{EDGE_TYPE} {{key: $key}}]->(:{NODE_LABEL})
WHERE r.workspace_id = $ws
WITH r
DELETE r
RETURN count(*)
"""

_REL_FILTER = """
WHERE r.workspace_id = $ws AND b.workspace_id = $ws
  AND ($edge_types IS NULL OR r.edge_type IN $edge_types)
  AND ($include_non_current OR r.is_current = true)
"""

_Q_RELATIONSHIPS = {
    "outgoing": f"""
MATCH (a:{NODE_LABEL} {{key: $key}}) WHERE a.workspace_id = $ws
MATCH (a)-[r:{EDGE_TYPE}]->(b:{NODE_LABEL})
{_REL_FILTER}
RETURN 'outgoing', properties(r), properties(b)
ORDER BY r.key LIMIT $limit
""",
    "incoming": f"""
MATCH (a:{NODE_LABEL} {{key: $key}}) WHERE a.workspace_id = $ws
MATCH (a)<-[r:{EDGE_TYPE}]-(b:{NODE_LABEL})
{_REL_FILTER}
RETURN 'incoming', properties(r), properties(b)
ORDER BY r.key LIMIT $limit
""",
    "both": f"""
MATCH (a:{NODE_LABEL} {{key: $key}}) WHERE a.workspace_id = $ws
MATCH (a)-[r:{EDGE_TYPE}]-(b:{NODE_LABEL})
{_REL_FILTER}
RETURN CASE WHEN startNode(r) = a THEN 'outgoing' ELSE 'incoming' END,
       properties(r), properties(b)
ORDER BY r.key LIMIT $limit
""",
}


def _public(props: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in dict(props).items() if k not in _INTERNAL_PROPS}


@dataclass
class RelationshipRow:
    direction: str
    edge: Dict[str, Any]
    neighbor: Dict[str, Any]


class ProjectionGraph:
    """Thin, explicit wrapper around a Semantica ``GraphStore``."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock = threading.Lock()
        self._schema_ready = False
        self._available: Optional[bool] = None  # last observed connectivity

        self._store = GraphStore(
            backend="falkordb",
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            password=settings.falkordb_password,
            graph_name=settings.graph_name,
        )
        # GraphStore/FalkorDBStore force the interactive progress tracker on,
        # which renders console progress bars for every query. It is purely
        # cosmetic; turn it off for a server process.
        get_progress_tracker().enabled = False

    # -- connection management -------------------------------------------

    def _connect_locked(self) -> None:
        """Connect the Semantica FalkorDB backend.

        Narrow, documented capability gap (semantica 0.6.0):
        ``FalkorDBStore.connect()`` does not forward socket timeouts to the
        falkordb client, so an unreachable host could block a request thread
        indefinitely. We construct the falkordb client with bounded timeouts
        and hand it to Semantica's own ``FalkorDBClient`` wrapper; every
        query still executes through ``GraphStore.execute_query``.
        """
        from falkordb import FalkorDB

        backend = self._store._store_backend
        timeout = self._settings.graph_timeout_seconds
        try:
            db = FalkorDB(
                host=self._settings.falkordb_host,
                port=self._settings.falkordb_port,
                password=self._settings.falkordb_password,
                socket_timeout=timeout,
                socket_connect_timeout=timeout,
                health_check_interval=30,
            )
        except Exception as exc:  # noqa: BLE001 - classify below
            raise GraphUnavailable("graph store unreachable") from exc
        backend._client = FalkorDBClient(db)
        backend._graph = None  # re-selected lazily on the new client

    def _ensure_connected(self) -> None:
        backend = self._store._store_backend
        if backend._client is not None:
            return
        with self._lock:
            if backend._client is None:
                self._connect_locked()

    def _mark_available(self, available: bool, exc: Optional[BaseException] = None) -> None:
        """Log connectivity transitions once, not on every failed request."""
        if self._available == available:
            return
        self._available = available
        if available:
            logger.info("graph store connected", extra={"event": "graph_connected"})
        else:
            root = exc
            while root is not None and (root.__cause__ or root.__context__):
                root = root.__cause__ or root.__context__
            logger.error(
                "graph store unreachable",
                extra={
                    "event": "graph_unavailable",
                    "error_type": type(root).__name__ if root else None,
                    "host": self._settings.falkordb_host,
                    "port": self._settings.falkordb_port,
                },
            )

    def _run(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        operation: str = "query",
        quiet: bool = False,
    ) -> List[List[Any]]:
        try:
            self._ensure_connected()
            result = self._store.execute_query(query, params or {})
        except GraphUnavailable as exc:
            self._mark_available(False, exc)
            raise
        except Exception as exc:  # noqa: BLE001 - classify below
            if _is_connection_failure(exc):
                self._mark_available(False, exc)
                raise GraphUnavailable("graph store unreachable") from exc
            if not quiet:
                # Operation name and exception type only: driver messages can
                # embed query text and parameter values.
                logger.error(
                    "graph operation failed",
                    extra={
                        "event": "graph_operation_failed",
                        "operation": operation,
                        "error_type": type(exc).__name__,
                    },
                )
            raise GraphError("graph operation failed") from exc
        self._mark_available(True)
        return result.get("records", [])

    def close(self) -> None:
        self._store.close()

    # -- readiness / schema ----------------------------------------------

    def ping(self) -> bool:
        rows = self._run(_Q_PING, operation="ping")
        return bool(rows) and rows[0][0] == 1

    def ensure_schema(self) -> None:
        """Create lookup indexes once. Idempotent."""
        if self._schema_ready:
            return
        for statement in _Q_INDEXES:
            try:
                self._run(statement, operation="create_index", quiet=True)
            except GraphError as exc:
                if "already indexed" not in str(exc.__cause__ or ""):
                    logger.error(
                        "graph index creation failed",
                        extra={"event": "graph_operation_failed", "operation": "create_index"},
                    )
                    raise
        self._schema_ready = True

    def check_ready(self) -> bool:
        try:
            ok = self.ping()
            if ok:
                self.ensure_schema()
            return ok
        except (GraphUnavailable, GraphError):
            return False

    # -- nodes -----------------------------------------------------------

    def upsert_node(
        self, workspace_id: str, canonical_id: str, props: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        self.ensure_schema()
        key = scoped_key(workspace_id, canonical_id)
        stored = {k: v for k, v in props.items() if v is not None}
        stored.update(key=key, workspace_id=workspace_id, canonical_id=canonical_id)
        rows = self._run(
            _Q_UPSERT_NODE,
            {"key": key, "ws": workspace_id, "props": stored, "now": _now()},
            operation="upsert_node",
        )
        if not rows:
            # Only possible if a node with this key exists under a different
            # workspace_id, i.e. the projection is corrupt. Refuse to touch it.
            logger.error("workspace scope conflict", extra={"event": "scope_conflict"})
            raise GraphError("workspace scope conflict")
        return _public(rows[0][0]), bool(rows[0][1])

    def get_node(self, workspace_id: str, canonical_id: str) -> Optional[Dict[str, Any]]:
        key = scoped_key(workspace_id, canonical_id)
        rows = self._run(_Q_GET_NODE, {"key": key, "ws": workspace_id}, operation="get_node")
        return _public(rows[0][0]) if rows else None

    def delete_node(self, workspace_id: str, canonical_id: str) -> Optional[int]:
        """Delete a node and every projection edge attached to it.
        Returns the number of detached edges, or None if the node was absent."""
        key = scoped_key(workspace_id, canonical_id)
        rows = self._run(_Q_DELETE_NODE, {"key": key, "ws": workspace_id}, operation="delete_node")
        return int(rows[0][0]) if rows else None

    # -- edges -----------------------------------------------------------

    def upsert_edge(
        self,
        workspace_id: str,
        edge_id: str,
        source_node_id: str,
        target_node_id: str,
        props: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], bool]:
        self.ensure_schema()
        key = scoped_key(workspace_id, edge_id)
        stored = {k: v for k, v in props.items() if v is not None}
        stored.update(
            key=key,
            workspace_id=workspace_id,
            edge_id=edge_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
        )
        rows = self._run(
            _Q_UPSERT_EDGE,
            {
                "key": key,
                "ws": workspace_id,
                "src_key": scoped_key(workspace_id, source_node_id),
                "tgt_key": scoped_key(workspace_id, target_node_id),
                "props": stored,
                "now": _now(),
            },
            operation="upsert_edge",
        )
        if not rows:
            raise EndpointNotFound("source or target node not found in workspace")
        return _public(rows[0][0]), bool(rows[0][1])

    def delete_edge(self, workspace_id: str, edge_id: str) -> bool:
        key = scoped_key(workspace_id, edge_id)
        rows = self._run(_Q_DELETE_EDGE, {"key": key, "ws": workspace_id}, operation="delete_edge")
        return bool(rows) and int(rows[0][0]) > 0

    # -- relationship query ----------------------------------------------

    def relationships(
        self,
        workspace_id: str,
        node_id: str,
        direction: str,
        edge_types: Optional[List[str]],
        include_non_current: bool,
        limit: int,
    ) -> Tuple[Optional[Dict[str, Any]], List[RelationshipRow], bool]:
        anchor = self.get_node(workspace_id, node_id)
        if anchor is None:
            return None, [], False
        template = _Q_RELATIONSHIPS[direction]  # KeyError impossible: validated Literal
        rows = self._run(
            template,
            {
                "key": scoped_key(workspace_id, node_id),
                "ws": workspace_id,
                "edge_types": edge_types,
                "include_non_current": include_non_current,
                "limit": int(limit) + 1,  # one extra row detects truncation
            },
            operation="query_relationships",
        )
        truncated = len(rows) > limit
        out = [
            RelationshipRow(direction=r[0], edge=_public(r[1]), neighbor=_public(r[2]))
            for r in rows[:limit]
        ]
        return anchor, out, truncated
