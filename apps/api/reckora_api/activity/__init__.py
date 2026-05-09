"""Per-dossier activity feed (Phase 5 step 3).

Aggregates the four event kinds the platform already persists —
``comment_added``, ``assigned``, ``shared``, ``anchored`` — into a
chronological newest-first stream, gated by the same access rules that
guard the dossier itself.
"""
