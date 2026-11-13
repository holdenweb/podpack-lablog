"""Conformance with the podpack app contract.

What makes this an app rather than a blueprint: that a site installs it by
naming it, that the site decides where it lands, that its tables reach alembic
with no Flask app involved, and that what it declares is what podpack checks.
Each of these fails silently or fails on somebody else's site when it breaks.
"""

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import HOST_CONFIG, SiteFactory, login

from podpack import Backup
from podpack.migrations import target_metadata
from podpack_lablog import DEFAULT_TIMEZONE, site_app

TABLES = {"lablog_project", "lablog_experiment", "lablog_entry"}


def test_the_app_names_itself_after_its_blueprint() -> None:
    """`lablog` keys [apps.lablog], [site.mounts], the templates and the role."""
    assert site_app.name == site_app.blueprint.name == "lablog"


def test_a_site_installs_it_by_naming_it(site: SiteFactory) -> None:
    app = site()
    assert login(app).get("/lablog/").status_code == 200
    assert app.extensions["podpack"].installed_from == {"lablog": "podpack_lablog"}


def test_it_wears_the_sites_chrome(holder: FlaskClient) -> None:
    body = holder.get("/lablog/").get_data(as_text=True)
    assert "<h2>Lab log</h2>" in body
    assert "Served by podpack" in body  # the default chrome, since this site ships none


def test_it_contributes_no_nav_entry(app: Flask) -> None:
    """Deliberately: a Section renders for every visitor, and this app is
    private, so an entry would advertise a page that answers 404."""
    assert site_app.nav == ()
    assert app.extensions["podpack"].nav == []
    assert "/lablog/" not in app.test_client().get("/").get_data(as_text=True)


def test_the_site_decides_where_it_lands(site: SiteFactory) -> None:
    app = site(
        host_config={"site": {**HOST_CONFIG["site"], "mounts": {"lablog": "/elsewhere"}}}
    )
    client = login(app)
    assert client.get("/elsewhere/").status_code == 200
    assert client.get("/lablog/").status_code == 404


def test_its_tables_reach_alembic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """What a site's migration sees, with no Flask app and none of its secrets.

    The one contract test that goes red when a model sits in a module nothing
    imports -- renaming models.py does not, because the views import it.
    """
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("SQLALCHEMY_DATABASE_URI", raising=False)
    config = tmp_path / "app.toml"
    config.write_text('[site]\nname = "t"\napps = ["podpack_lablog"]\n')
    assert TABLES <= set(target_metadata(config).tables)


def test_it_declares_the_tables_it_joins_to() -> None:
    assert site_app.needs_tables == frozenset({"user", "role", "roles_users"})


def test_it_vouches_that_it_stores_nothing_on_disk(app: Flask) -> None:
    """Everything is in the database, and podpack holds the app to that."""
    assert site_app.backs_up == Backup(data=False)
    data_dir = app.extensions["podpack"].data_root / "lablog"
    assert data_dir.is_dir() and not any(data_dir.iterdir())


def test_its_time_zone_comes_from_the_site(app: Flask, holder: FlaskClient) -> None:
    """The silent one: a mis-keyed [apps.lablog] returns {} and nothing is
    raised, so the packaged default would simply apply for ever."""
    assert app.config["LABLOG_TIMEZONE"] == ZoneInfo("Asia/Tokyo")
    holder.post("/lablog/new", data={"name": "Zoned"})
    assert "JST" in holder.get("/lablog/").get_data(as_text=True)


def test_a_site_without_the_setting_gets_the_packaged_default(site: SiteFactory) -> None:
    app = site(host_config={"apps": {}})
    assert app.config["LABLOG_TIMEZONE"] == ZoneInfo(DEFAULT_TIMEZONE)
    client = login(app)
    client.post("/lablog/new", data={"name": "Unzoned"})
    assert "UTC" in client.get("/lablog/").get_data(as_text=True)


def test_an_unknown_time_zone_stops_the_site_booting(site: SiteFactory) -> None:
    """At boot rather than on the first page that shows a time."""
    with pytest.raises(RuntimeError, match=r"\[apps\.lablog\] timezone = 'Mars/Olympus'"):
        site(host_config={"apps": {"lablog": {"timezone": "Mars/Olympus"}}})


def test_status_reports_who_can_use_it(app: Flask) -> None:
    with app.app_context():
        assert site_app.status() == {"role": "lablog", "role_exists": True, "holders": 1}


def test_status_says_when_nobody_can(site: SiteFactory) -> None:
    """Before `flask roles create lablog`, every page 404s for everyone."""
    app = site(role=False)
    with app.app_context():
        assert site_app.status() == {"role": "lablog", "role_exists": False, "holders": 0}
