"""The lab log: a testing history for each project.

Each experiment starts with what is expected to happen, accumulates
observations -- some of them Claude's output, pasted in -- and ends with a
conclusion. A project's page is its complete log.

A podpack app is a package exposing one module-level `site_app`; the rest is
convention (models.py, templates/lablog/). See writing-an-app.md in podpack.
"""

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from flask import Flask

from podpack import Backup, SiteApp, User, app_config, db
from podpack.auth import Role

from .views import ROLE, blueprint

DEFAULT_TIMEZONE = "UTC"


def _init(app: Flask) -> None:
    """Settle the display time zone before the first request.

    Resolved here so that a zone the host does not know stops the site booting,
    rather than breaking the first page that shows a time. `app_config` needs
    an app context and, with no request to resolve the app from, our name.
    """
    with app.app_context():
        settings = app_config("lablog")
    name = settings.get("timezone", DEFAULT_TIMEZONE)
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise RuntimeError(
            f"[apps.lablog] timezone = {name!r} is not a time zone this host "
            "knows; use an IANA name such as 'Europe/London'"
        ) from exc
    app.config["LABLOG_TIMEZONE"] = zone


class LablogApp(SiteApp):
    def status(self) -> dict[str, Any]:
        """Whether anybody can use this app at all.

        Every page answers 404 to a user without the role, and so to everyone
        while the role is missing or ungranted -- the owner included, with
        nothing saying why. That is the failure ADR-0033 records holdenweb.com
        having with `/_status` itself, so it is reported here rather than left
        to be rediscovered.
        """
        role_exists = bool(
            db.session.scalar(sa.select(sa.func.count(Role.id)).where(Role.name == ROLE))
        )
        holders = db.session.scalar(
            sa.select(sa.func.count(User.id)).where(User.roles.any(Role.name == ROLE))
        )
        return {"role": ROLE, "role_exists": role_exists, "holders": holders}


site_app = LablogApp(
    blueprint=blueprint,
    url_prefix="/lablog",
    # No nav entry. A Section has only a label and an endpoint, so it renders
    # for every visitor, and on a site with public pages that would advertise a
    # private tool to people who can only ever get a 404 from it.
    nav=(),
    init=_init,
    # podpack defines these; this app joins to all three -- `user` for authors,
    # `role` and `roles_users` for the access check and `status()`.
    needs_tables=frozenset({"user", "role", "roles_users"}),
    # Everything is in the database. Saying so lets a backup tell an app that
    # stores nothing from one whose mount never arrived, and podpack checks it.
    backs_up=Backup(data=False),
)
