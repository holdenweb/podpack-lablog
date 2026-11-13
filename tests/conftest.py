"""Fixtures that build a real podpack site with this app installed.

The shape is podpack-notes', which is the shape writing-an-app.md Part 3
prescribes: a real site on SQLite with real per-app directories, and real
users who sign in through flask-security's own form. Three callers matter
here rather than notes' two, because access turns on a role: someone who holds
it, someone signed in who does not, and someone not signed in at all.
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from flask_security import hash_password
from sqlalchemy import event
from sqlalchemy.engine import Engine

from podpack import create_app

SiteFactory = Callable[..., Flask]

# Asia/Tokyo because it has never observed daylight saving, so a test can
# assert "JST" on any date of the year. UTC, the packaged default, would be
# indistinguishable from a time zone setting that never arrived.
HOST_CONFIG: dict[str, Any] = {
    "site": {"name": "test site", "environment": "test", "apps": ["podpack_lablog"]},
    "apps": {"lablog": {"timezone": "Asia/Tokyo"}},
}

HOLDER = "holder@example.com"
STRANGER = "stranger@example.com"
PASSWORD = "test-password"


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    """SQLite ignores REFERENCES unless told otherwise, per connection.

    Without this every foreign key here exists in the schema and constrains
    nothing, so the suite could not tell a correction pointing at a real entry
    from one pointing at nothing.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SiteFactory:
    """A podpack site with this app installed, roots pointed at tmp_path.

    The merge is one level deep, so a `host_config` override replaces a whole
    top-level table: override `site` and restate `apps` with it.
    """
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
    monkeypatch.setenv("SECURITY_PASSWORD_SALT", "test-password-salt")

    def _build(*, role: bool = True, **overrides: Any) -> Flask:
        config = {**HOST_CONFIG, **overrides.pop("host_config", {})}
        app = create_app(
            {
                "TESTING": True,
                # Without this flask-security's login form refuses the test
                # client's POST for want of a CSRF token. test_access.py turns
                # it back on to show this app's forms enforce one.
                "WTF_CSRF_ENABLED": False,
                # example.com has no MX record, and flask-security looks.
                "SECURITY_EMAIL_VALIDATOR_ARGS": {"check_deliverability": False},
            },
            host_config=config,
            data_root=tmp_path / "data",
            log_root=tmp_path / "logs",
            **overrides,
        )
        with app.app_context():
            from podpack import db

            db.create_all()
            _create_users(role)
        return app

    return _build


def _create_users(role: bool) -> None:
    """HOLDER, who has the `lablog` role, and STRANGER, who does not.

    `role=False` builds the site as it is before anyone has run
    `flask roles create lablog`, which is what `status()` exists to report.
    """
    from podpack import db
    from podpack.auth import user_datastore

    users = {
        email: user_datastore.create_user(
            email=email,
            password=hash_password(PASSWORD),
            active=True,
            confirmed_at=datetime.now(timezone.utc),
        )
        for email in (HOLDER, STRANGER)
    }
    if role:
        user_datastore.add_role_to_user(
            users[HOLDER], user_datastore.find_or_create_role("lablog")
        )
    db.session.commit()


def login(app: Flask, email: str = HOLDER) -> FlaskClient:
    """A client holding a real session cookie, obtained through `/login`.

    The session is inspected rather than the status code, because a failed
    login also answers 200.
    """
    client = app.test_client()
    client.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=True)
    with client.session_transaction() as session:
        assert "_user_id" in session, f"logging in as {email} did not take"
    return client


@pytest.fixture
def app(site: SiteFactory) -> Flask:
    return site()


@pytest.fixture
def holder(app: Flask) -> FlaskClient:
    return login(app, HOLDER)


@pytest.fixture
def stranger(app: Flask) -> FlaskClient:
    return login(app, STRANGER)


# -- driving the app the way a browser does ----------------------------------


def make_project(client: FlaskClient, name: str = "Podpack PDF", description: str = "") -> str:
    """Create a project through the form; return its slug."""
    response = client.post("/lablog/new", data={"name": name, "description": description})
    assert response.status_code == 302, response.get_data(as_text=True)
    return response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]


def start_experiment(
    client: FlaskClient,
    slug: str,
    title: str = "Booklet from a scan",
    expectation: str = "A 12-page booklet comes back.",
) -> int:
    """Start an experiment through the form; return its number."""
    response = client.post(
        f"/lablog/{slug}/experiments/new", data={"title": title, "expectation": expectation}
    )
    assert response.status_code == 302, response.get_data(as_text=True)
    return int(response.headers["Location"].rsplit("/", 1)[-1])


def add_entry(
    client: FlaskClient,
    slug: str,
    number: int,
    body: str = "It came back with 11 pages.",
    kind: str = "observation",
    corrects: int = 0,
) -> Any:
    return client.post(
        f"/lablog/{slug}/experiments/{number}/entries",
        data={"kind": kind, "body": body, "corrects": str(corrects)},
    )


def conclude(
    client: FlaskClient,
    slug: str,
    number: int,
    outcome: str = "not_as_expected",
    text: str = "Blank last pages are dropped.",
) -> Any:
    return client.post(
        f"/lablog/{slug}/experiments/{number}/conclusion",
        data={"outcome": outcome, "conclusion": text},
    )
