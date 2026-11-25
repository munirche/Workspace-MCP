"""Entry point: the MCP app and its listener on the Unix socket."""

import logging
import os
import sys
from pathlib import Path

import uvicorn

from workspace_mcp import alerts, app, auth, config

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.toml"
STATE_PATH = Path("/var/lib/workspace-mcp/tokens.db")
SOCKET_PATH = "/run/workspace-mcp/mcp.sock"


def main(environ=os.environ):
    # Logging first: the SDK's constructor sets up its own handler unless one exists.
    app.configure_logging()
    logger = logging.getLogger("workspace_mcp")
    try:
        loaded = config.load_file(CONFIG_PATH, environ)
        try:
            sender = alerts.TelegramSender(
                loaded.secrets.telegram_bot_token.reveal(), loaded.secrets.telegram_chat_id.reveal()
            )
        except ValueError:
            raise config.ConfigError("the alert bot token or chat id is malformed") from None
        service = app.build(
            loaded, store=auth.SqliteStore(STATE_PATH), alerts=alerts.Queue(alerts.Alerts(sender, logger))
        )
    except config.ConfigError as error:
        # Its text names what is wrong and never a value.
        logger.error("refused to start: %s", error)
        return 1
    except Exception as error:
        # Any other text might carry a value (pydantic prints its input), so the class alone.
        logger.error("refused to start (%s)", type(error).__name__)
        return 1
    uvicorn.Server(server_config(service.app)).run()
    return 0


def server_config(asgi):
    # No proxy headers (the one X-Forwarded-For value is read by the service),
    # no access log (it would record /authorize's query), one process.
    return uvicorn.Config(
        asgi, uds=SOCKET_PATH, proxy_headers=False, access_log=False, log_config=None, lifespan="on", workers=1
    )


if __name__ == "__main__":
    sys.exit(main())
