"""What only a local trial may change: MCP Inspector's origin, and the trial script's throwaway values."""

import importlib.machinery
import importlib.util
import os
import stat
from pathlib import Path

import pytest
from conftest import PUBLIC

from workspace_mcp import app, auth
from workspace_mcp.config import ConfigError

INSPECTOR = "http://localhost:6274"
TRIAL_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "local_trial.py"


def _script():
    loader = importlib.machinery.SourceFileLoader("local_trial", str(TRIAL_SCRIPT))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def test_a_public_service_takes_no_browser_origin(make_flow):
    """Covers: R-5.8, R-7.9"""
    with pytest.raises(ConfigError, match="trial"):
        make_flow(public_url=PUBLIC, trial=app.Trial(origin=INSPECTOR))


@pytest.mark.parametrize("origin", ["https://localhost:6274", "http://attacker.example", "http://localhost:6274/",
                                    "http://localhost", "*"])
def test_a_trial_origin_is_one_exact_local_origin(make_flow, origin):
    """Covers: R-5.8"""
    with pytest.raises(ConfigError, match="trial"):
        make_flow(trial=app.Trial(origin=origin))


def test_without_a_trial_any_browser_origin_is_refused(flow):
    """Covers: R-7.9"""
    token = flow.sign_in()["access_token"]
    for origin in ("https://claude.ai", INSPECTOR):
        assert flow.mcp(token, headers={"origin": origin}).status_code == 403
    assert flow.mcp(token).status_code == 200


def test_a_trial_lets_inspector_reach_the_tool_and_token_endpoints(make_flow):
    """Covers: R-5.8"""
    flow = make_flow(trial=app.Trial(return_address=INSPECTOR + "/oauth/callback", origin=INSPECTOR))
    for path in ("/token", "/mcp"):
        preflight = flow.http.options(path, headers={"origin": INSPECTOR,
                                                     "access-control-request-method": "POST",
                                                     "access-control-request-headers": "authorization, content-type"})
        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == INSPECTOR
        assert "authorization" in preflight.headers["access-control-allow-headers"]
    token = flow.sign_in()["access_token"]
    response = flow.mcp(token, headers={"origin": INSPECTOR})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == INSPECTOR
    refused = flow.mcp(None, headers={"origin": INSPECTOR})
    assert refused.status_code == 401
    assert "www-authenticate" in refused.headers["access-control-expose-headers"]
    other = flow.http.options("/token", headers={"origin": "http://attacker.example",
                                                 "access-control-request-method": "POST",
                                                 "access-control-request-headers": "authorization"})
    assert other.status_code != 204
    assert "authorization" not in other.headers.get("access-control-allow-headers", "").lower()


def test_the_trial_script_makes_fresh_throwaway_values_and_reads_none_from_the_shell(monkeypatch):
    """Covers: R-5.8, R-9.1"""
    monkeypatch.setenv("WORKSPACE_MCP_CLIENT_SECRET", "marker-from-the-shell")
    monkeypatch.setenv("WORKSPACE_MCP_PUBLIC_URL", PUBLIC)
    trial = _script()
    first, second = trial.prepare(8765), trial.prepare(8765, INSPECTOR + "/oauth/callback", INSPECTOR)
    try:
        for prepared in (first, second):
            assert stat.S_IMODE(os.stat(prepared.folder).st_mode) == 0o700
            assert prepared.config.public.issuer == "http://localhost:8765"
            assert prepared.client_secret != "marker-from-the-shell"
            assert len(prepared.passphrase) >= 20
            assert prepared.service.window.current() is not None
        assert first.client_secret != second.client_secret and first.passphrase != second.passphrase
        assert first.service.provider._client.return_address == "http://127.0.0.1:6276/oauth/callback"
        assert second.service.provider._client.return_address == INSPECTOR + "/oauth/callback"
        assert first.store.generation_path != auth.GENERATION_PATH
    finally:
        for prepared in (first, second):
            prepared.close()
    assert not Path(first.folder).exists()
