"""Asks for the sign-in passphrase twice and prints its hash, for WORKSPACE_MCP_PASSPHRASE_HASH."""

# Run it in your own terminal, never in an assistant's session: what it prints is a secret.

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Code"))

from workspace_mcp import login  # noqa: E402


def main(argv, stdin=sys.stdin, prompt=getpass.getpass):
    if len(argv) != 1:
        print("hash_passphrase.py takes no arguments; it asks for the passphrase", file=sys.stderr)
        return 2
    if not stdin.isatty():
        print("run it in a terminal, so the passphrase is typed and never piped", file=sys.stderr)
        return 2
    first = prompt("Passphrase: ")
    if prompt("Again: ") != first:
        print("the two entries differ; nothing was printed", file=sys.stderr)
        return 1
    try:
        print(login.hash_passphrase(first))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
