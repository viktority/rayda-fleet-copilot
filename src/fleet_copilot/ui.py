"""Loopback-only launcher. Creates a temporary, single-company browser credential."""

import json
import os
import secrets
import socket
import threading
import time
import webbrowser

import uvicorn

from .api import create_app
from .config import Settings
from .domain import FleetError, Principal


def launch(company: str, role: str, port: int, open_browser: bool = True):
    # Reserve the port before replacing a running launcher's access file.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
        except OSError:
            raise FleetError(
                "port_unavailable", f"Port {port} is unavailable. Choose another --port."
            ) from None
        _run(company, role, listener, open_browser)


def _run(company: str, role: str, listener: socket.socket, open_browser: bool):
    port = listener.getsockname()[1]
    settings = Settings.load()
    store = settings.store()
    principal = Principal(company_id=company, actor_id="local-ui", role=role)
    store.rows(principal)  # Fail clearly before opening a browser for an unknown tenant.
    token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}"
    access_url = f"{url}/#access={token}"
    access_path = settings.database.parent / f"ui-access-{port}.json"
    # Refuse symlinks; this file contains an ephemeral local API credential, not the OpenAI key.
    fd = os.open(access_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        os.chmod(access_path, 0o600)
        json.dump({"url": access_url, "company_id": company, "role": role}, stream)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(store, {token: principal}), host="127.0.0.1", port=port, log_level="warning"
        )
    )

    def open_when_ready():
        for _ in range(100):
            if server.started:
                webbrowser.open(access_url)
                return
            if server.should_exit:
                return
            time.sleep(0.1)

    if open_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    print(f"Fleet Copilot: {url} — {company}, {role}. Actions are simulated.", flush=True)
    print(f"Private browser connection saved to {access_path}. Stop with Ctrl+C.", flush=True)
    try:
        server.run(sockets=[listener])
    finally:
        # Only remove the file owned by this invocation; don't remove a newer launcher's file.
        try:
            if json.loads(access_path.read_text()).get("url") == access_url:
                access_path.unlink()
        except (OSError, ValueError):
            pass
