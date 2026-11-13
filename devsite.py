"""A throwaway site with nothing installed but this app, for running it by hand.

Not part of the package. An app cannot run on a bare Flask app -- `db`,
`app_config()` and `base.html` all come from the framework -- so the smallest
thing that can host it is a site (writing-an-app.md, Part 2).

    uv run flask --app devsite seed     # two users and the roles, once
    uv run flask --app devsite run -p 5001

`seed` makes holder@example.com, who has the `lablog` and `admin` roles, and
stranger@example.com, who has neither, both with the password in DEV_PASSWORD.

`admin` is there so that `/_status` -- where this app's `status()` reports on
its role -- answers the holder. It also silences podpack's boot warning about
the missing role, which would otherwise tell you to run
`flask --app podpack roles create admin`. That command is wrong here: podpack
names the Flask app's import name, which is `podpack` for a site with no
package of its own, and `--app podpack` then looks for the container's
/etc/podpack/app.toml. On this site every command is `--app devsite`.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
DEV = HERE / "devsite"

# The secrets podpack insists on, as dev-only values. Read when create_app runs,
# so setting them before the import is convention rather than necessity.
os.environ.setdefault("SECRET_KEY", "test-devsite-secret-key")
os.environ.setdefault("SECURITY_PASSWORD_SALT", "test-devsite-password-salt")
os.environ.setdefault("SQLALCHEMY_DATABASE_URI", f"sqlite:///{DEV / 'dev.db'}")
os.environ.setdefault("PODPACK_DATA_ROOT", str(DEV / "data"))
os.environ.setdefault("PODPACK_LOG_ROOT", str(DEV / "logs"))

import click  # noqa: E402
from flask import Flask  # noqa: E402
from flask_security import hash_password  # noqa: E402

from podpack import create_app as _create_site  # noqa: E402
from podpack import db  # noqa: E402

HOLDER = "holder@example.com"
STRANGER = "stranger@example.com"
DEV_PASSWORD = "test-devsite-password"


def create_app() -> Flask:
    """podpack's default chrome, SQLite, and `create_all` rather than alembic:
    schema authoring belongs to a real site, not to this one."""
    app = _create_site(
        # example.com has no MX record, and flask-security checks for one
        # before it will create a user with that address.
        {"SECURITY_EMAIL_VALIDATOR_ARGS": {"check_deliverability": False}},
        config_path=DEV / "app.toml",
    )
    with app.app_context():
        db.create_all()

    @app.cli.command("seed")
    def seed() -> None:
        """Create the dev users and the roles, if they are not there already."""
        from podpack import ADMIN_ROLE
        from podpack.auth import user_datastore

        roles = [
            user_datastore.find_or_create_role(name) for name in ("lablog", ADMIN_ROLE)
        ]
        for email in (HOLDER, STRANGER):
            user = user_datastore.find_user(email=email)
            if user is None:
                user = user_datastore.create_user(
                    email=email,
                    password=hash_password(DEV_PASSWORD),
                    active=True,
                    confirmed_at=datetime.now(timezone.utc),
                )
            if email == HOLDER:
                for role in roles:
                    user_datastore.add_role_to_user(user, role)
        db.session.commit()
        click.echo(f"{HOLDER} (roles lablog, admin) and {STRANGER} (no roles) are ready")

    return app
