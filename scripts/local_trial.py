"""Runs the service on this box for T1.11's trial with MCP Inspector, with throwaway values and no real alert."""

# Every value here is made fresh for this run and thrown away after it, so a
# session may see them; none is read from the shell's WORKSPACE_MCP_* names.

import argparse
import logging
import os
import secrets
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Code"))

import uvicorn  # noqa: E402

from workspace_mcp import alerts, app, auth, config, login  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
# Inspector v2's command line listens here for the sign-in's return (its default).
CALLBACK = "http://127.0.0.1:6276/oauth/callback"


@dataclass
class Prepared:
    folder: str
    socket: str
    config: object
    store: object
    service: object
    client_id: str
    client_secret: str
    passphrase: str

    def close(self):
        shutil.rmtree(self.folder, ignore_errors=True)


def prepare(port, callback=CALLBACK, origin=None):
    folder = tempfile.mkdtemp(prefix="workspace-mcp-trial-")
    try:
        return _prepare(folder, port, callback, origin)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _prepare(folder, port, callback, origin):
    client_id, client_secret = "trial-" + secrets.token_hex(8), secrets.token_urlsafe(24)
    passphrase = secrets.token_urlsafe(18)
    environ = {
        "WORKSPACE_MCP_PUBLIC_URL": f"http://localhost:{port}",
        "WORKSPACE_MCP_PASSPHRASE_HASH": login.hash_passphrase(passphrase),
        "WORKSPACE_MCP_CLIENT_ID": client_id,
        "WORKSPACE_MCP_CLIENT_SECRET": client_secret,
        "WORKSPACE_MCP_TELEGRAM_BOT_TOKEN": "not-used-in-the-trial",
        "WORKSPACE_MCP_TELEGRAM_CHAT_ID": "0",
    }
    loaded = config.load_file(REPO / "config.toml", environ)
    generation = Path(folder, "generation")
    generation.write_text("0\n")
    flag = Path(folder, "open")
    flag.write_text(f"{secrets.token_hex(32)} {int(time.time())}\n")
    flag.chmod(0o644)
    logger = logging.getLogger("workspace_mcp")
    store = auth.SqliteStore(Path(folder, "tokens.db"), str(generation))
    queue = alerts.Queue(alerts.Alerts(lambda text: logger.warning("alert (trial, not sent): %s", text), logger))
    trial = app.Trial(return_address=callback, flag_path=str(flag), owner=os.getuid(), origin=origin)
    service = app.build(loaded, store=store, alerts=queue, trial=trial)
    return Prepared(folder, str(Path(folder, "mcp.sock")), loaded, store, service, client_id, client_secret,
                    passphrase)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="the laptop's end of the SSH tunnel")
    parser.add_argument("--callback", default=CALLBACK, help="Inspector's return address, exactly")
    parser.add_argument("--origin", help="Inspector's page origin, only for its browser UI (v1)")
    arguments = parser.parse_args(argv[1:])
    app.configure_logging()
    prepared = prepare(arguments.port, arguments.callback, arguments.origin)
    try:
        print(f"""
Socket:        {prepared.socket}
On the laptop: ssh -N -L {arguments.port}:{prepared.socket} openclaw-vps
Server URL:    http://localhost:{arguments.port}/mcp   (localhost exactly: 127.0.0.1 is refused)
  Client ID:     {prepared.client_id}
  Client secret: {prepared.client_secret}
  Return URL:    {arguments.callback}
Passphrase:    {prepared.passphrase}
The sign-in window is open for ten minutes from now; start again for another.
""", flush=True)
        uvicorn.Server(uvicorn.Config(prepared.service.app, uds=prepared.socket, proxy_headers=False,
                                      access_log=False, log_config=None, lifespan="on", workers=1)).run()
    finally:
        prepared.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
