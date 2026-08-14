from fastapi.testclient import TestClient
import numpy as np

from backend.app import app
from backend import services


def test_health() -> None:
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_resolve_tic_without_remote_request() -> None:
    response = TestClient(app).get("/api/targets/resolve", params={"q": "TIC 261136679"})
    assert response.status_code == 200
    assert response.json()["tic_id"] == "261136679"


def test_resolve_common_star_name_through_catalog_aliases(monkeypatch) -> None:
    monkeypatch.setattr(services, "_resolve_tic_target", lambda target: None)
    monkeypatch.setattr(services, "request_json", lambda *args, **kwargs: {
        "manifest": {"lookup_status": "OK", "resolved_name": "Proxima Cen"},
        "system": {"objects": {"stellar_set": {"stars": {
            "Proxima Cen": {
                "requested_object": "True",
                "alias_set": {
                    "default_name": "Proxima Cen",
                    "aliases": ["GJ 551", "TIC 388857263", "Proxima Centauri"],
                },
            }
        }}}},
    })

    result = services.resolve_target("Proxima Centauri")

    assert result["tic_id"] == "388857263"
    assert result["resolved_name"] == "Proxima Cen"


def test_hyphenated_catalog_name_is_not_mistaken_for_tic_id(monkeypatch) -> None:
    monkeypatch.setattr(services, "_resolve_tic_target", lambda target: None)
    monkeypatch.setattr(services, "_resolve_catalog_alias", lambda target: {
        "input": target,
        "tic_id": "267667295",
        "resolved_name": "KOI-351",
    })

    result = services.resolve_target("Kepler-90")

    assert result["tic_id"] == "267667295"
    assert result["resolved_name"] == "KOI-351"


def test_resolve_general_star_through_tic_catalog(monkeypatch) -> None:
    monkeypatch.setattr(services, "_resolve_catalog_alias", lambda target: None)
    monkeypatch.setattr(services, "_resolve_tic_target", lambda target: {
        "input": target,
        "tic_id": "322899250",
        "resolved_name": "Sirius A",
    })

    result = services.resolve_target("Sirius A")

    assert result["tic_id"] == "322899250"
    assert result["resolved_name"] == "Sirius A"


def test_catalog_match_follows_legacy_tic_alias(monkeypatch) -> None:
    def fake_tap(query: str) -> list[dict[str, str]]:
        if "388857263" in query and "pscomppars" in query:
            return [{"hostname": "Proxima Cen", "pl_name": "Proxima Cen b"}]
        return []

    monkeypatch.setattr(services, "_tap", fake_tap)
    monkeypatch.setattr(services, "_resolve_catalog_alias", lambda target: {
        "input": target,
        "tic_id": "388857263",
        "resolved_name": "Proxima Cen",
    })
    services.catalog_matches.cache_clear()

    result = services.catalog_matches("1019422535", "Proxima Centauri")

    assert result["status"] == "confirmed"
    assert result["host_name"] == "Proxima Cen"


def test_inspection_uses_confirmed_planet_host_name(monkeypatch) -> None:
    monkeypatch.setattr(services, "fetch_tess_lightcurve", lambda *args, **kwargs: (
        np.linspace(0, 20, 100), np.ones(100), "test curve"
    ))
    monkeypatch.setattr(services, "find_repeating_dip", lambda *args, **kwargs: {
        "period_days": 2.0,
        "duration_days": 0.1,
        "transit_time": 1.0,
        "bls_power": 1.0,
    })
    monkeypatch.setattr(services, "catalog_matches", lambda *args: {
        "status": "confirmed",
        "host_name": "WASP-46",
        "planets": [{"hostname": "WASP-46", "pl_name": "WASP-46 b"}],
        "tois": [],
    })

    result = services.inspect_target("TIC 231663901")

    assert result["target"]["resolved_name"] == "WASP-46"
    assert result["target"]["tic_id"] == "231663901"
