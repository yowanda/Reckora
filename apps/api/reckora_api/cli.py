"""Typer entrypoint for the Reckora API.

Two commands:

- ``reckora-api serve`` — run uvicorn against the FastAPI app.
- ``reckora-api create-user`` — bootstrap or create a user from the shell so
  the operator can log in without first hitting the registration endpoint.
"""

from __future__ import annotations

from getpass import getpass
from typing import Annotated

import typer
import uvicorn

from reckora_api.auth.passwords import hash_password
from reckora_api.auth.repository import UserRepository
from reckora_api.config import APISettings

app = typer.Typer(
    help="Reckora API — manage and run the HTTP backend.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port.")] = 8000,
    reload: Annotated[
        bool,
        typer.Option("--reload", help="Reload on code changes (dev only)."),
    ] = False,
) -> None:
    """Start the FastAPI server.

    Reads :class:`APISettings` from environment / ``.env``. ``RECKORA_API_JWT_SECRET``
    must be set or the server refuses to start.
    """
    s = APISettings()
    if not s.jwt_secret:
        raise typer.BadParameter(
            "RECKORA_API_JWT_SECRET must be set in the environment before starting "
            "the server. Generate one with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`."
        )
    uvicorn.run(
        "reckora_api.main:create_app",
        host=host,
        port=port,
        reload=reload,
        factory=True,
    )


@app.command(name="create-user")
def create_user(
    username: Annotated[str, typer.Argument(help="Username (3-64 chars, [A-Za-z0-9_-]).")],
    password: Annotated[
        str | None,
        typer.Option(
            "--password",
            help="Password (if omitted, you will be prompted; never echoed).",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Insert a user into the API's SQLite store.

    Useful for bootstrapping the first account or scripting headless setup.
    Idempotent only via uniqueness — a duplicate username will fail loudly.
    """
    if password is None:
        password = getpass("password: ")
    if len(password) < 8:
        raise typer.BadParameter("password must be at least 8 characters")
    s = APISettings()
    with UserRepository(s.db_path) as repo:
        if repo.get_by_username(username) is not None:
            raise typer.BadParameter(f"username {username!r} already exists")
        record = repo.create_user(
            username=username,
            password_hash=hash_password(password),
        )
    typer.echo(f"created user {record.username} (id={record.id})")


if __name__ == "__main__":
    app()
