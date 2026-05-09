/**
 * Strongly-typed views of the loose `dict[str, Any]` shapes that
 * `SavedDossierPayload` exposes through OpenAPI.
 *
 * The backend returns `to_dossier_dict()` (see
 * `src/reckora/reports/json_export.py`) which produces fully-shaped
 * Pydantic dumps for `Subject`, `Trace`, `TimelineEntry`, `Anomaly`,
 * `Edge` and the `ai` block. Surfacing those shapes here lets the
 * Dossier renderer work against real fields instead of `unknown`
 * indexed reads scattered through the components.
 */
import type { SavedDossierPayload } from "@/api/types";

export interface RawIdentifier {
  type: string;
  value: string;
}

export interface RawEvidence {
  source_url: string;
  fetched_at: string;
  payload_sha256: string;
  archive_url: string | null;
  screenshot_path: string | null;
  raw_payload: Record<string, unknown> | null;
}

export interface RawTrace {
  identifier: RawIdentifier;
  source: string;
  fields: Record<string, unknown>;
  evidence: RawEvidence;
}

export interface RawTimelineEntry {
  timestamp: string;
  source: string;
  identifier_type: string;
  identifier_value: string;
  evidence_sha256: string;
  source_url: string;
  archive_url: string | null;
  screenshot_path: string | null;
}

export type AnomalySeverity = "low" | "medium" | "high";

export interface RawAnomaly {
  kind: string;
  severity: AnomalySeverity | string;
  message: string;
  supporting_evidence: string[];
}

export interface RawEdge {
  source: RawIdentifier;
  target: RawIdentifier;
  kind: string;
  confidence: number;
  reasons: string[];
  supporting_evidence: string[];
}

export interface RawAnchorReceipt {
  calendar_url: string;
  submitted_at: string;
}

export interface RawAnchor {
  merkle_root: string;
  leaf_hashes?: string[];
  created_at: string;
  receipts?: RawAnchorReceipt[];
}

export interface RawSubject {
  id: string;
  seed_identifier: RawIdentifier;
  identifiers?: RawIdentifier[];
  traces?: RawTrace[];
}

export interface RawAi {
  summary?: string | null;
  hypotheses?: string | null;
}

/**
 * Narrow `SavedDossierPayload` (where every nested block is
 * `{[key: string]: unknown}`) into the typed shape the Dossier
 * renderer needs.
 */
export interface DossierView {
  id: string;
  created_at: string;
  subject: RawSubject;
  traces: RawTrace[];
  timeline: RawTimelineEntry[];
  anomalies: RawAnomaly[];
  edges: RawEdge[];
  ai: RawAi;
  anchor: RawAnchor | null;
  owner_username: string | null;
}

export function viewDossier(payload: SavedDossierPayload): DossierView {
  return {
    id: payload.id,
    created_at: payload.created_at,
    subject: payload.subject as unknown as RawSubject,
    traces: (payload.traces ?? []) as unknown as RawTrace[],
    timeline: (payload.timeline ?? []) as unknown as RawTimelineEntry[],
    anomalies: (payload.anomalies ?? []) as unknown as RawAnomaly[],
    edges: (payload.edges ?? []) as unknown as RawEdge[],
    ai: (payload.ai ?? {}) as unknown as RawAi,
    anchor: (payload.anchor ?? null) as unknown as RawAnchor | null,
    owner_username: payload.owner_username ?? null,
  };
}
