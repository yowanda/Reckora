"""Investigation endpoints: collect / list / get / dossier / delete."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING
from unittest.mock import patch

from fastapi.testclient import TestClient

from reckora.models.entity import Trace

if TYPE_CHECKING:
    from reckora.evidence.anchor import Anchor


def test_create_investigation_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    )
    assert response.status_code == 401


def test_create_investigation_runs_orchestrator_and_persists(
    authed_client: TestClient,
) -> None:
    response = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"].startswith("subj-")
    assert len(body["traces"]) == 1
    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["identifier_value"] == "alice"
    assert body["anomalies"] == []
    assert body["subject"]["seed_identifier"]["value"] == "alice"

    listed = authed_client.get("/api/v1/subjects").json()
    assert any(row["id"] == body["id"] for row in listed)


def test_create_investigation_with_extras(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/api/v1/investigations",
        json={
            "seed": {"kind": "username", "value": "alice"},
            "extras": [{"kind": "username", "value": "al1ce"}],
        },
    )
    assert response.status_code == 201, response.text
    values = {i["value"] for i in response.json()["subject"]["identifiers"]}
    assert {"alice", "al1ce"}.issubset(values)


def test_create_investigation_with_breach_flag(authed_client: TestClient) -> None:
    """``breach: true`` plumbs an extra HIBP collector into ``investigate``.

    We swap :func:`reckora_api.investigations.routes._build_breach_collector`
    for a fake so the test doesn't need a live HIBP key or network access.
    The fake emits a deterministic Trace whose presence we assert on.
    """
    from datetime import UTC, datetime

    from reckora.collectors.base import Collector
    from reckora.evidence.chain import make_evidence
    from reckora.models.entity import Identifier, Trace
    from reckora.models.enums import IdentifierType, TraceSource

    class _FakeBreachCollector(Collector):
        name = "fake_breach"
        supported = frozenset({IdentifierType.EMAIL.value})

        async def collect(self, identifier: Identifier) -> list[Trace]:
            evidence = make_evidence(
                "https://haveibeenpwned.test/api/v3/breachedaccount/x",
                {"breaches": []},
                keep_raw=False,
                fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            return [
                Trace(
                    identifier=identifier,
                    source=TraceSource.BREACH_HIBP,
                    fields={
                        "email": identifier.value,
                        "breach_count": 1,
                        "first_breach_date": "2013-10-04",
                        "latest_breach_date": "2013-10-04",
                        "data_classes": ["Email addresses"],
                        "has_sensitive_breach": False,
                        "breaches": [
                            {"name": "Adobe", "breach_date": "2013-10-04"},
                        ],
                    },
                    evidence=evidence,
                )
            ]

    with patch(
        "reckora_api.investigations.routes._build_breach_collector",
        return_value=_FakeBreachCollector(),
    ):
        response = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "email", "value": "alice@example.com"},
                "breach": True,
            },
        )
    assert response.status_code == 201, response.text
    body = response.json()
    breach_traces = [t for t in body["traces"] if t["source"] == "breach_hibp"]
    assert len(breach_traces) == 1
    assert breach_traces[0]["fields"]["breach_count"] == 1


def test_create_investigation_breach_wires_web_search_fn_when_credentialed(
    authed_client: TestClient,
) -> None:
    """``breach: true`` resolves a web-search backend and threads it into doc-leak.

    The CLI's `--breach` path passes a :data:`WebSearchFn` resolved from
    ``OPENAI_API_KEY`` or ChatGPT OAuth into :class:`DocLeakCollector` so
    the eight SPA / anti-bot platforms can route their searches through
    OpenAI's Responses ``web_search`` tool. This test asserts the HTTP
    API does the same: when the server has at least one auth backend
    configured, ``_build_doc_leak_collector`` receives a non-``None``
    ``web_search_fn`` keyword argument rather than the bare default.

    We monkeypatch :func:`reckora.auth.storage.load_credentials` to
    return a fake OAuth credential (mirroring ``reckora auth login``)
    and stub ``_build_doc_leak_collector`` / ``_build_breach_collector``
    so the test never goes to the network.
    """
    from datetime import UTC, datetime, timedelta

    from reckora.auth.oauth import OAuthCredentials
    from reckora.collectors.base import Collector
    from reckora.models.entity import Identifier

    captured: dict[str, object] = {}

    class _NoopCollector(Collector):
        name = "noop"
        supported = frozenset({"username", "email"})

        async def collect(self, identifier: Identifier) -> list[Trace]:
            return []

    def _capture_doc_leak(*, web_search_fn: object = None) -> Collector:
        captured["web_search_fn"] = web_search_fn
        return _NoopCollector()

    fake_creds = OAuthCredentials(
        access_token="fake-access",
        refresh_token="fake-refresh",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        id_token=None,
    )

    with (
        patch(
            "reckora_api.investigations.routes.load_credentials",
            return_value=fake_creds,
        ),
        patch(
            "reckora_api.investigations.routes._build_doc_leak_collector",
            side_effect=_capture_doc_leak,
        ),
        patch(
            "reckora_api.investigations.routes._build_breach_collector",
            return_value=_NoopCollector(),
        ),
    ):
        response = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "email", "value": "alice@example.com"},
                "breach": True,
            },
        )

    assert response.status_code == 201, response.text
    assert "web_search_fn" in captured, "_build_doc_leak_collector was not called"
    assert captured["web_search_fn"] is not None, (
        "doc-leak collector was constructed without a web_search_fn; the eight "
        "SPA / anti-bot platforms would emit `unverified` traces."
    )


def test_create_investigation_breach_passes_none_when_unconfigured(
    authed_client: TestClient,
) -> None:
    """No auth backend → ``_build_doc_leak_collector`` gets ``web_search_fn=None``.

    Mirrors the CLI's behaviour: the doc-leak collector falls back to
    direct-probe-only mode (archive.org, pdfcoffee, yumpu, pastebin)
    rather than raising, so a breach investigation still completes on
    hosts without an OpenAI key or ChatGPT OAuth session.
    """
    from reckora.collectors.base import Collector
    from reckora.models.entity import Identifier

    captured: dict[str, object] = {}

    class _NoopCollector(Collector):
        name = "noop"
        supported = frozenset({"username", "email"})

        async def collect(self, identifier: Identifier) -> list[Trace]:
            return []

    def _capture_doc_leak(*, web_search_fn: object = None) -> Collector:
        captured["web_search_fn"] = web_search_fn
        return _NoopCollector()

    with (
        patch(
            "reckora_api.investigations.routes.load_credentials",
            return_value=None,
        ),
        patch(
            "reckora_api.investigations.routes.engine_settings",
        ) as engine_settings_mock,
        patch(
            "reckora_api.investigations.routes._build_doc_leak_collector",
            side_effect=_capture_doc_leak,
        ),
        patch(
            "reckora_api.investigations.routes._build_breach_collector",
            return_value=_NoopCollector(),
        ),
    ):
        engine_settings_mock.openai_api_key = None
        response = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "email", "value": "alice@example.com"},
                "breach": True,
            },
        )

    assert response.status_code == 201, response.text
    assert captured.get("web_search_fn") is None


def test_create_investigation_with_screenshot_flag(authed_client: TestClient) -> None:
    """`screenshot: true` swaps the orchestrator's screenshotter for a fake.

    We monkeypatch :func:`reckora_api.investigations.routes._build_screenshotter`
    so the test never tries to launch real Chromium — Playwright is an
    optional, browser-binary-heavy dependency that is intentionally absent
    from CI.
    """

    shot = "/screenshots/alice.png"

    class _FakeShotter:
        async def screenshot(self, source_url: str) -> str | None:
            return shot

        async def aclose(self) -> None:
            return None

    with patch(
        "reckora_api.investigations.routes._build_screenshotter",
        return_value=_FakeShotter(),
    ):
        response = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "username", "value": "alice"},
                "screenshot": True,
            },
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["traces"][0]["evidence"]["screenshot_path"] == shot


def test_create_investigation_unknown_identifier_kind_422(
    authed_client: TestClient,
) -> None:
    response = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "nonsense", "value": "alice"}},
    )
    assert response.status_code == 422


def test_list_subjects_empty(authed_client: TestClient) -> None:
    response = authed_client.get("/api/v1/subjects")
    assert response.status_code == 200
    assert response.json() == []


def test_list_subjects_returns_summaries(authed_client: TestClient) -> None:
    authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    )
    authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "bob"}},
    )
    rows = authed_client.get("/api/v1/subjects").json()
    assert len(rows) == 2
    seed_values = {r["seed"]["value"] for r in rows}
    assert seed_values == {"alice", "bob"}
    for row in rows:
        assert row["trace_count"] == 1
        assert row["edge_count"] == 0
        assert row["has_summary"] is False


def test_list_subjects_respects_limit(authed_client: TestClient) -> None:
    for name in ("alice", "bob", "carol"):
        authed_client.post(
            "/api/v1/investigations",
            json={"seed": {"kind": "username", "value": name}},
        )
    rows = authed_client.get("/api/v1/subjects", params={"limit": 2}).json()
    assert len(rows) == 2


def test_list_subjects_rejects_invalid_limit(authed_client: TestClient) -> None:
    response = authed_client.get("/api/v1/subjects", params={"limit": 0})
    assert response.status_code == 422


def test_get_subject_returns_full_dossier(authed_client: TestClient) -> None:
    created = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = authed_client.get(f"/api/v1/subjects/{created['id']}")
    assert response.status_code == 200, response.text
    fetched = response.json()
    assert fetched["id"] == created["id"]
    assert fetched["traces"] == created["traces"]
    assert fetched["timeline"] == created["timeline"]
    assert fetched["anomalies"] == created["anomalies"]
    assert fetched["subject"] == created["subject"]


def test_get_subject_unknown_returns_404(authed_client: TestClient) -> None:
    response = authed_client.get("/api/v1/subjects/subj-doesnotexist")
    assert response.status_code == 404


def test_get_subject_dossier_html(authed_client: TestClient) -> None:
    created = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = authed_client.get(
        f"/api/v1/subjects/{created['id']}/dossier",
        params={"format": "html"},
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<!DOCTYPE html>" in response.text
    assert "alice" in response.text


def test_get_subject_dossier_markdown(authed_client: TestClient) -> None:
    created = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = authed_client.get(
        f"/api/v1/subjects/{created['id']}/dossier",
        params={"format": "md"},
    )
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    assert "# Reckora dossier" in response.text
    assert "## Timeline" in response.text
    assert "## Anomalies" in response.text


def test_get_subject_dossier_json(authed_client: TestClient) -> None:
    created = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = authed_client.get(
        f"/api/v1/subjects/{created['id']}/dossier",
        params={"format": "json"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["subject"]["seed_identifier"]["value"] == "alice"
    assert "timeline" in body
    assert body["timeline"][0]["identifier_value"] == "alice"
    assert "anomalies" in body
    assert body["anomalies"] == []


def test_get_subject_dossier_pdf(authed_client: TestClient) -> None:
    created = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = authed_client.get(
        f"/api/v1/subjects/{created['id']}/dossier",
        params={"format": "pdf"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert created["id"] in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")
    assert b"%%EOF" in response.content[-32:]


def test_get_subject_dossier_rejects_unknown_format(authed_client: TestClient) -> None:
    created = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = authed_client.get(
        f"/api/v1/subjects/{created['id']}/dossier",
        params={"format": "xml"},
    )
    assert response.status_code == 422


def test_get_subject_dossier_unknown_id_404(authed_client: TestClient) -> None:
    response = authed_client.get(
        "/api/v1/subjects/subj-doesnotexist/dossier",
        params={"format": "html"},
    )
    assert response.status_code == 404


def test_delete_subject(authed_client: TestClient) -> None:
    created = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = authed_client.delete(f"/api/v1/subjects/{created['id']}")
    assert response.status_code == 204
    follow_up = authed_client.get(f"/api/v1/subjects/{created['id']}")
    assert follow_up.status_code == 404


def test_delete_subject_unknown_returns_404(authed_client: TestClient) -> None:
    response = authed_client.delete("/api/v1/subjects/subj-doesnotexist")
    assert response.status_code == 404


def test_subjects_endpoints_require_auth(client: TestClient) -> None:
    for verb_url in (
        ("get", "/api/v1/subjects"),
        ("get", "/api/v1/subjects/whatever"),
        ("get", "/api/v1/subjects/whatever/dossier"),
        ("delete", "/api/v1/subjects/whatever"),
    ):
        verb, url = verb_url
        response = getattr(client, verb)(url)
        assert response.status_code == 401, f"{verb} {url} expected 401, got {response.status_code}"


def test_openapi_advertises_versioned_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    expected = {
        "/api/v1/auth/register",
        "/api/v1/auth/token",
        "/api/v1/auth/me",
        "/api/v1/investigations",
        "/api/v1/subjects",
        "/api/v1/subjects/{subject_id}",
        "/api/v1/subjects/{subject_id}/dossier",
        "/healthz",
    }
    assert expected.issubset(paths.keys())


def test_openapi_payload_is_valid_json(client: TestClient) -> None:
    raw = client.get("/openapi.json").content
    assert json.loads(raw)["info"]["title"] == "Reckora API"


# ---------------------------------------------------------------------------
# Layer 7: ``anchor: true`` request flag.
#
# Mirrors the ``--anchor`` flag on the CLI: minting a Merkle root over the
# collected traces, soliciting OpenTimestamps receipts, persisting the
# anchor, and surfacing it on every dossier endpoint. We patch
# ``reckora_api.investigations.routes.anchor_traces`` so tests are
# hermetic — they never go to the public OpenTimestamps calendars.
# ---------------------------------------------------------------------------


def _stub_anchor_traces() -> Callable[[Sequence[Trace]], Awaitable[Anchor]]:
    """Build a deterministic stand-in for ``anchor_traces`` that derives the
    Merkle root from the supplied traces (so verify-anchor-style checks
    still pass) and returns one canned calendar receipt.
    """
    from datetime import UTC, datetime

    from reckora.evidence.anchor import Anchor
    from reckora.evidence.merkle import compute_dossier_root
    from reckora.evidence.timestamp import CalendarReceipt

    async def _fake(traces: Sequence[Trace]) -> Anchor:
        root, leaves = compute_dossier_root(traces)
        return Anchor(
            merkle_root=root,
            leaf_hashes=leaves,
            receipts=[
                CalendarReceipt(
                    calendar_url="https://stub.calendar.example",
                    receipt_b64="ZmFrZQ==",
                    submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            ],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    return _fake


def test_create_investigation_with_anchor_flag(authed_client: TestClient) -> None:
    """``anchor: true`` triggers ``anchor_traces`` and surfaces the resulting
    Merkle root + calendar receipts on the response payload."""
    with patch(
        "reckora_api.investigations.routes.anchor_traces",
        side_effect=_stub_anchor_traces(),
    ):
        response = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "username", "value": "alice"},
                "anchor": True,
            },
        )
    assert response.status_code == 201, response.text
    body = response.json()
    anchor = body["anchor"]
    assert anchor is not None
    assert len(anchor["merkle_root"]) == 64
    leaves = anchor["leaf_hashes"]
    assert leaves == [body["traces"][0]["evidence"]["payload_sha256"]]
    assert [r["calendar_url"] for r in anchor["receipts"]] == ["https://stub.calendar.example"]


def test_create_investigation_without_anchor_omits_field(authed_client: TestClient) -> None:
    """The default request must NOT mint an anchor — anchoring is opt-in."""
    response = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    )
    assert response.status_code == 201, response.text
    assert response.json()["anchor"] is None


def test_create_investigation_anchor_with_no_traces_returns_422(
    authed_client: TestClient,
) -> None:
    """Anchoring a run that produced zero traces is meaningless — the API
    must reject it with 422 rather than silently emitting a root over an
    empty set. We force "no traces" by seeding with an identifier kind
    the fake collector does not support (``email``)."""
    response = authed_client.post(
        "/api/v1/investigations",
        json={
            "seed": {"kind": "email", "value": "alice@example.com"},
            "anchor": True,
        },
    )
    assert response.status_code == 422, response.text
    assert "anchor" in response.json()["detail"].lower()


def test_list_subjects_includes_has_anchor(authed_client: TestClient) -> None:
    """``has_anchor`` on the list summary must reflect whether the dossier
    was saved with a Merkle anchor or not."""
    with patch(
        "reckora_api.investigations.routes.anchor_traces",
        side_effect=_stub_anchor_traces(),
    ):
        anchored_id = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "username", "value": "alice"},
                "anchor": True,
            },
        ).json()["id"]
    plain_id = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "bob"}},
    ).json()["id"]

    rows = authed_client.get("/api/v1/subjects").json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[anchored_id]["has_anchor"] is True
    assert by_id[plain_id]["has_anchor"] is False


def test_get_subject_returns_persisted_anchor(authed_client: TestClient) -> None:
    """The single-subject GET must round-trip the anchor that was minted at
    investigation time (proves the SQLite anchor row reloads cleanly)."""
    with patch(
        "reckora_api.investigations.routes.anchor_traces",
        side_effect=_stub_anchor_traces(),
    ):
        created = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "username", "value": "alice"},
                "anchor": True,
            },
        ).json()

    fetched = authed_client.get(f"/api/v1/subjects/{created['id']}").json()
    assert fetched["anchor"] == created["anchor"]
    assert fetched["anchor"]["merkle_root"] == created["anchor"]["merkle_root"]


def test_get_subject_dossier_html_renders_anchor(authed_client: TestClient) -> None:
    with patch(
        "reckora_api.investigations.routes.anchor_traces",
        side_effect=_stub_anchor_traces(),
    ):
        created = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "username", "value": "alice"},
                "anchor": True,
            },
        ).json()
    response = authed_client.get(
        f"/api/v1/subjects/{created['id']}/dossier",
        params={"format": "html"},
    )
    assert response.status_code == 200
    body = response.text
    assert "Cross-trace anchor" in body
    assert created["anchor"]["merkle_root"] in body
    assert "stub.calendar.example" in body


def test_get_subject_dossier_markdown_renders_anchor(authed_client: TestClient) -> None:
    with patch(
        "reckora_api.investigations.routes.anchor_traces",
        side_effect=_stub_anchor_traces(),
    ):
        created = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "username", "value": "alice"},
                "anchor": True,
            },
        ).json()
    response = authed_client.get(
        f"/api/v1/subjects/{created['id']}/dossier",
        params={"format": "md"},
    )
    assert response.status_code == 200
    md = response.text
    assert "## Cross-trace anchor" in md
    assert f"merkle_root: `{created['anchor']['merkle_root']}`" in md
    assert "stub.calendar.example" in md


def test_get_subject_dossier_json_renders_anchor(authed_client: TestClient) -> None:
    with patch(
        "reckora_api.investigations.routes.anchor_traces",
        side_effect=_stub_anchor_traces(),
    ):
        created = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "username", "value": "alice"},
                "anchor": True,
            },
        ).json()
    response = authed_client.get(
        f"/api/v1/subjects/{created['id']}/dossier",
        params={"format": "json"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["anchor"]["merkle_root"] == created["anchor"]["merkle_root"]
    assert body["anchor"]["leaf_hashes"] == created["anchor"]["leaf_hashes"]
    assert [r["calendar_url"] for r in body["anchor"]["receipts"]] == [
        "https://stub.calendar.example"
    ]


def test_get_subject_dossier_pdf_renders_with_anchor(authed_client: TestClient) -> None:
    """The PDF endpoint must still render successfully when an anchor is
    present — we can't easily extract anchor text from binary PDF, but a
    crashy PDF renderer is the most likely regression."""
    with patch(
        "reckora_api.investigations.routes.anchor_traces",
        side_effect=_stub_anchor_traces(),
    ):
        created = authed_client.post(
            "/api/v1/investigations",
            json={
                "seed": {"kind": "username", "value": "alice"},
                "anchor": True,
            },
        ).json()
    response = authed_client.get(
        f"/api/v1/subjects/{created['id']}/dossier",
        params={"format": "pdf"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


def test_get_subject_dossier_html_omits_anchor_when_absent(
    authed_client: TestClient,
) -> None:
    """A dossier saved without anchoring must not show a phantom
    'Cross-trace anchor' section — guards against an accidentally
    unconditional render."""
    created = authed_client.post(
        "/api/v1/investigations",
        json={"seed": {"kind": "username", "value": "alice"}},
    ).json()
    response = authed_client.get(
        f"/api/v1/subjects/{created['id']}/dossier",
        params={"format": "html"},
    )
    assert response.status_code == 200
    assert "Cross-trace anchor" not in response.text
