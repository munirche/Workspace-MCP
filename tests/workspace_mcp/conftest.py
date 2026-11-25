"""Shared pieces for the tests that drive the service through its own routes."""

import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from workspace_mcp import app, auth, config

CONFIG = """
[repos.claude-config]
source = "checkout"
path = "/srv/claude-config"

[users.munir]
read = ["claude-config"]
"""

LOCAL = "http://localhost:8765"
PUBLIC = "https://mcp.example.test"


def environ(public_url=LOCAL):
    # Built at run time, so the repo's credential scan never sees a hash here.
    salt, digest = base64.b64encode(b"s" * 16).decode(), base64.b64encode(b"h" * 64).decode()
    return {
        "WORKSPACE_MCP_PUBLIC_URL": public_url,
        "WORKSPACE_MCP_PASSPHRASE_HASH": "$".join(["scr" + "ypt", "16384", "8", "1", salt, digest]),
        "WORKSPACE_MCP_CLIENT_ID": "client-" + secrets.token_hex(8),
        "WORKSPACE_MCP_CLIENT_SECRET": secrets.token_urlsafe(24),
        "WORKSPACE_MCP_TELEGRAM_BOT_TOKEN": "token",
        "WORKSPACE_MCP_TELEGRAM_CHAT_ID": "chat",
    }


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


class Flow:
    """One built service and the steps of a sign-in against it."""

    def __init__(self, public_url=LOCAL, text=CONFIG, trial=None, secret=None):
        self.environ = environ(public_url)
        if secret is not None:
            self.environ["WORKSPACE_MCP_CLIENT_SECRET"] = secret
        self.config = config.load(text, self.environ)
        self.clock = Clock()
        self.store = auth.MemoryStore()
        self.service = app.build(self.config, store=self.store, clock=self.clock, trial=trial)
        self.client_id = self.environ["WORKSPACE_MCP_CLIENT_ID"]
        self.secret = self.environ["WORKSPACE_MCP_CLIENT_SECRET"]
        self.return_address = trial.return_address if trial else auth.CLAUDE_CALLBACK
        self.http = TestClient(self.service.app, base_url=self.config.public.issuer, follow_redirects=False)

    def authorize(self, challenge, **params):
        defaults = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.return_address,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "state-1",
        }
        defaults.update(params)
        return self.http.get("/authorize", params={k: v for k, v in defaults.items() if v is not None})

    def request_id(self, response):
        assert response.status_code == 302, response.text
        location = response.headers["location"]
        assert location.split("?")[0] == self.config.public.issuer + "/login"
        return query(location)["request"]

    def code(self, challenge, **params):
        redirect = self.service.provider.approve(self.request_id(self.authorize(challenge, **params)))
        return query(redirect)["code"]

    def exchange(self, code, verifier, **fields):
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": self.return_address,
            "client_id": self.client_id,
            "client_secret": self.secret,
        }
        form.update(fields)
        return self.http.post("/token", data={k: v for k, v in form.items() if v is not None})

    def sign_in(self, **params):
        verifier, challenge = pkce()
        response = self.exchange(self.code(challenge, **params), verifier)
        assert response.status_code == 200, response.text
        return response.json()

    def refresh(self, refresh_token, **fields):
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.secret,
        }
        form.update(fields)
        return self.http.post("/token", data={k: v for k, v in form.items() if v is not None})

    def mcp(self, token=None, headers=None):
        sent = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
        if token is not None:
            sent["authorization"] = f"Bearer {token}"
        sent.update(headers or {})
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        return self.http.post("/mcp", json=body, headers=sent)


@pytest.fixture
def flow():
    built = Flow()
    with built.http:
        yield built


@pytest.fixture
def make_flow():
    opened = []

    def make(**kwargs):
        built = Flow(**kwargs)
        built.http.__enter__()
        opened.append(built)
        return built

    yield make
    for built in opened:
        built.http.__exit__(None, None, None)
