"""Optional Neo4j implementation of :class:`SubjectRepository`.

This adapter is the same shape as :class:`SQLiteSubjectRepository` so callers
can swap backends behind the seam without touching the orchestrator, CLI, or
API.

It is *opt-in* — the official ``neo4j`` driver is **not** in the default
install path. Pull it with ``uv sync --extra neo4j`` (or ``pip install
'reckora[neo4j]'``) before using this module.

Graph mapping
-------------

::

    (:Subject {id, seed_kind, seed_value, identifiers_json,
              created_at, summary_md, hypotheses_md})
        -[:HAS_IDENTIFIER {seed: bool}]-> (:Identifier {kind, value})
        -[:HAS_TRACE {idx, trace_json}]-> ()    -- via TraceNode child
        -[:HAS_EDGE  {idx, edge_json}]-> ()    -- via EdgeNode child

* ``Subject.id`` is the primary key (uniqueness constraint).
* ``Identifier`` nodes are *shared* across subjects (MERGE keyed on
  ``(kind, value)``). That is the one piece of cross-subject indexing the
  relational schema cannot express, and it is where the Neo4j adapter earns
  its keep — every dossier that mentions ``email:bob@example.com`` will
  attach to the same node, so a follow-up Cypher query ``MATCH (i:Identifier
  {value: 'bob@example.com'})<-[:HAS_IDENTIFIER]-(s:Subject) RETURN s`` lists
  every investigation that ever touched that identifier.
* ``TraceNode`` / ``EdgeNode`` are subject-owned and replaced atomically on
  each ``save``. Their ``trace_json`` / ``edge_json`` properties carry the
  canonical Pydantic JSON dump so a round-trip through the adapter preserves
  bit-for-bit fidelity with the in-memory models — bumping a model
  incompatibly will fail validation on read instead of silently corrupting
  the dossier (same contract as the SQLite adapter).

The repository does **not** own the driver lifecycle: callers construct (and
``close``) the driver themselves. That keeps the repo cheap to instantiate
per-request inside FastAPI without re-establishing TCP connections.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..models.entity import Edge, Identifier, Subject, Trace
from ..models.enums import IdentifierType
from .repository import SavedDossier, SavedDossierSummary


class Neo4jSubjectRepository:
    """Driver-backed Neo4j repository.

    Parameters
    ----------
    driver:
        An already-connected ``neo4j.Driver`` (or any object with the same
        ``session`` / ``close`` shape — the in-memory test fake passes a
        minimal stand-in).
    database:
        Optional database name (Neo4j 4+ multi-database). Falls back to the
        driver's default when ``None``.
    """

    def __init__(self, driver: Any, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database
        self._ensure_constraints()

    def _session(self) -> Any:
        if self._database is not None:
            return self._driver.session(database=self._database)
        return self._driver.session()

    def _ensure_constraints(self) -> None:
        with self._session() as s:
            s.run(
                "CREATE CONSTRAINT subject_id_unique IF NOT EXISTS "
                "FOR (sub:Subject) REQUIRE sub.id IS UNIQUE"
            )
            s.run(
                "CREATE CONSTRAINT identifier_unique IF NOT EXISTS "
                "FOR (i:Identifier) REQUIRE (i.kind, i.value) IS UNIQUE"
            )

    def save(
        self,
        *,
        subject: Subject,
        traces: list[Trace],
        edges: list[Edge],
        summary: str | None = None,
        hypotheses: str | None = None,
        created_at: datetime | None = None,
    ) -> SavedDossierSummary:
        ts = (created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        identifiers_json = json.dumps(
            [i.model_dump(mode="json") for i in subject.identifiers],
            ensure_ascii=False,
            sort_keys=True,
        )
        seed = subject.seed_identifier
        identifiers_payload = [
            {
                "kind": i.type.value,
                "value": i.value,
                "seed": (i.type == seed.type and i.value == seed.value),
            }
            for i in subject.identifiers
        ]
        traces_payload = [
            {"idx": idx, "trace_json": t.model_dump_json()} for idx, t in enumerate(traces)
        ]
        edges_payload = [
            {"idx": idx, "edge_json": e.model_dump_json()} for idx, e in enumerate(edges)
        ]
        with self._session() as s:
            s.execute_write(
                _save_tx,
                {
                    "id": subject.id,
                    "seed_kind": seed.type.value,
                    "seed_value": seed.value,
                    "identifiers_json": identifiers_json,
                    "created_at": ts,
                    "summary": summary,
                    "hypotheses": hypotheses,
                    "identifiers": identifiers_payload,
                    "traces": traces_payload,
                    "edges": edges_payload,
                },
            )
        return SavedDossierSummary(
            id=subject.id,
            seed_identifier=seed,
            created_at=datetime.fromisoformat(ts),
            identifier_count=len(subject.identifiers),
            trace_count=len(traces),
            edge_count=len(edges),
            has_summary=summary is not None,
            has_hypotheses=hypotheses is not None,
        )

    def get(self, subject_id: str) -> SavedDossier | None:
        with self._session() as s:
            data = s.execute_read(_get_tx, subject_id)
        if data is None:
            return None
        seed = Identifier(
            type=IdentifierType(data["seed_kind"]),
            value=data["seed_value"],
        )
        identifiers = [
            Identifier(type=IdentifierType(d["type"]), value=d["value"])
            for d in json.loads(data["identifiers_json"])
        ]
        traces = [Trace.model_validate_json(t) for t in data["traces"]]
        edges = [Edge.model_validate_json(e) for e in data["edges"]]
        subject = Subject(
            id=subject_id,
            seed_identifier=seed,
            identifiers=identifiers,
            traces=traces,
        )
        return SavedDossier(
            id=subject_id,
            subject=subject,
            traces=traces,
            edges=edges,
            created_at=datetime.fromisoformat(data["created_at"]),
            summary=data["summary"],
            hypotheses=data["hypotheses"],
        )

    def list_recent(self, limit: int = 20) -> list[SavedDossierSummary]:
        if limit <= 0:
            return []
        with self._session() as s:
            rows = s.execute_read(_list_recent_tx, limit)
        out: list[SavedDossierSummary] = []
        for row in rows:
            seed = Identifier(
                type=IdentifierType(row["seed_kind"]),
                value=row["seed_value"],
            )
            ids_data = json.loads(row["identifiers_json"])
            out.append(
                SavedDossierSummary(
                    id=row["id"],
                    seed_identifier=seed,
                    created_at=datetime.fromisoformat(row["created_at"]),
                    identifier_count=len(ids_data),
                    trace_count=int(row["trace_count"]),
                    edge_count=int(row["edge_count"]),
                    has_summary=row["summary_md"] is not None,
                    has_hypotheses=row["hypotheses_md"] is not None,
                )
            )
        return out

    def delete(self, subject_id: str) -> bool:
        with self._session() as s:
            removed: bool = s.execute_write(_delete_tx, subject_id)
        return removed


# ---------------------------------------------------------------------------
# Transaction functions. Defined at module scope (rather than as methods) so
# the official ``neo4j`` driver's ``execute_write`` / ``execute_read`` can
# pickle them across worker threads.
# ---------------------------------------------------------------------------


def _save_tx(tx: Any, payload: dict[str, Any]) -> None:
    """Single-transaction upsert of one dossier (subject + children)."""
    sid = payload["id"]
    tx.run(
        """
        MERGE (sub:Subject {id: $id})
        SET sub.seed_kind = $seed_kind,
            sub.seed_value = $seed_value,
            sub.identifiers_json = $identifiers_json,
            sub.created_at = $created_at,
            sub.summary_md = $summary,
            sub.hypotheses_md = $hypotheses
        """,
        id=sid,
        seed_kind=payload["seed_kind"],
        seed_value=payload["seed_value"],
        identifiers_json=payload["identifiers_json"],
        created_at=payload["created_at"],
        summary=payload["summary"],
        hypotheses=payload["hypotheses"],
    )
    # Replace subject-owned children atomically. Identifier nodes are NOT
    # deleted because they are shared across subjects — only the relationship
    # from this subject is removed.
    tx.run(
        "MATCH (sub:Subject {id: $id})-[:HAS_TRACE]->(t:TraceNode) DETACH DELETE t",
        id=sid,
    )
    tx.run(
        "MATCH (sub:Subject {id: $id})-[:HAS_EDGE]->(e:EdgeNode) DETACH DELETE e",
        id=sid,
    )
    tx.run(
        "MATCH (sub:Subject {id: $id})-[r:HAS_IDENTIFIER]->() DELETE r",
        id=sid,
    )
    if payload["identifiers"]:
        tx.run(
            """
            UNWIND $items AS item
            MATCH (sub:Subject {id: $id})
            MERGE (i:Identifier {kind: item.kind, value: item.value})
            MERGE (sub)-[r:HAS_IDENTIFIER]->(i)
            SET r.seed = item.seed
            """,
            id=sid,
            items=payload["identifiers"],
        )
    if payload["traces"]:
        tx.run(
            """
            UNWIND $items AS item
            MATCH (sub:Subject {id: $id})
            CREATE (sub)-[:HAS_TRACE]->(:TraceNode {idx: item.idx, trace_json: item.trace_json})
            """,
            id=sid,
            items=payload["traces"],
        )
    if payload["edges"]:
        tx.run(
            """
            UNWIND $items AS item
            MATCH (sub:Subject {id: $id})
            CREATE (sub)-[:HAS_EDGE]->(:EdgeNode {idx: item.idx, edge_json: item.edge_json})
            """,
            id=sid,
            items=payload["edges"],
        )


def _get_tx(tx: Any, subject_id: str) -> dict[str, Any] | None:
    result = tx.run(
        """
        MATCH (sub:Subject {id: $id})
        OPTIONAL MATCH (sub)-[:HAS_TRACE]->(t:TraceNode)
        WITH sub, t ORDER BY t.idx
        WITH sub, collect(t.trace_json) AS traces
        OPTIONAL MATCH (sub)-[:HAS_EDGE]->(e:EdgeNode)
        WITH sub, traces, e ORDER BY e.idx
        WITH sub, traces, collect(e.edge_json) AS edges
        RETURN sub.seed_kind         AS seed_kind,
               sub.seed_value        AS seed_value,
               sub.identifiers_json  AS identifiers_json,
               sub.created_at        AS created_at,
               sub.summary_md        AS summary,
               sub.hypotheses_md     AS hypotheses,
               traces, edges
        """,
        id=subject_id,
    )
    record = result.single()
    if record is None:
        return None
    return {
        "seed_kind": record["seed_kind"],
        "seed_value": record["seed_value"],
        "identifiers_json": record["identifiers_json"],
        "created_at": record["created_at"],
        "summary": record["summary"],
        "hypotheses": record["hypotheses"],
        "traces": [t for t in record["traces"] if t is not None],
        "edges": [e for e in record["edges"] if e is not None],
    }


def _list_recent_tx(tx: Any, limit: int) -> list[dict[str, Any]]:
    result = tx.run(
        """
        MATCH (sub:Subject)
        OPTIONAL MATCH (sub)-[:HAS_TRACE]->(t:TraceNode)
        WITH sub, count(t) AS trace_count
        OPTIONAL MATCH (sub)-[:HAS_EDGE]->(e:EdgeNode)
        WITH sub, trace_count, count(e) AS edge_count
        RETURN sub.id                AS id,
               sub.seed_kind         AS seed_kind,
               sub.seed_value        AS seed_value,
               sub.identifiers_json  AS identifiers_json,
               sub.created_at        AS created_at,
               sub.summary_md        AS summary_md,
               sub.hypotheses_md     AS hypotheses_md,
               trace_count,
               edge_count
        ORDER BY sub.created_at DESC, sub.id DESC
        LIMIT $limit
        """,
        limit=limit,
    )
    return [
        {
            "id": rec["id"],
            "seed_kind": rec["seed_kind"],
            "seed_value": rec["seed_value"],
            "identifiers_json": rec["identifiers_json"],
            "created_at": rec["created_at"],
            "summary_md": rec["summary_md"],
            "hypotheses_md": rec["hypotheses_md"],
            "trace_count": rec["trace_count"],
            "edge_count": rec["edge_count"],
        }
        for rec in result
    ]


def _delete_tx(tx: Any, subject_id: str) -> bool:
    """Two-step delete that returns whether a row was actually removed.

    We can't ``RETURN count(sub)`` after ``DETACH DELETE sub`` because the
    bound variable is gone — so check existence first, then delete.
    """
    rec = tx.run(
        "MATCH (sub:Subject {id: $id}) RETURN count(sub) AS n",
        id=subject_id,
    ).single()
    if rec is None or int(rec["n"]) == 0:
        return False
    tx.run(
        """
        MATCH (sub:Subject {id: $id})
        OPTIONAL MATCH (sub)-[:HAS_TRACE]->(t:TraceNode)
        OPTIONAL MATCH (sub)-[:HAS_EDGE]->(e:EdgeNode)
        DETACH DELETE sub, t, e
        """,
        id=subject_id,
    )
    return True
