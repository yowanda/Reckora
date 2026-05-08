"""Investigation endpoints: collect / list / get / dossier / delete."""

from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient


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
