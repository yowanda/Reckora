"""Phase 5 step 14 — per-actor TODO checklist on dossiers.

A dossier-scoped, per-actor checklist. Items are private to the
calling actor and never surface to other readers (including the
owner). Useful for the analyst's own \"things I still want to
chase down\" list without polluting the shared comment thread.

Surface:

* ``GET /api/v1/subjects/{id}/todos/me`` &rarr; list (oldest
  first).
* ``POST /api/v1/subjects/{id}/todos/me`` &rarr; create.
* ``PATCH /api/v1/subjects/{id}/todos/me/{todo_id}`` &rarr; partial
  update (toggle ``done`` and/or rewrite ``body``).
* ``DELETE /api/v1/subjects/{id}/todos/me/{todo_id}`` &rarr;
  idempotent delete.

Cross-actor probing is collapsed into a 404 so an analyst cannot
discover the existence of another actor's items even by guessing
ids.
"""

from __future__ import annotations

from reckora_api.todos.routes import todos_router
from reckora_api.todos.schemas import TodoCreate, TodoEntry, TodoUpdate

__all__ = [
    "TodoCreate",
    "TodoEntry",
    "TodoUpdate",
    "todos_router",
]
