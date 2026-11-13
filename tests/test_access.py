"""Who may reach what.

The site this was built for allows public sign-up, so the claim worth testing
is not just "the role holder gets in" but "a confirmed stranger reaches nothing
an anonymous visitor cannot". The sweep below walks every rule the blueprint
registers rather than a list written by hand, so a view added later without
the guard turns this file red instead of quietly joining the public site.
"""

from typing import Any

import pytest
from flask import Flask, url_for
from flask.testing import FlaskClient

from conftest import add_entry, make_project, start_experiment

from podpack_lablog.models import Project
from podpack import db

SAMPLE_ARGS: dict[str, Any] = {"slug": "podpack-pdf", "number": 1}


def _routes(app: Flask) -> list[tuple[str, str]]:
    """(method, url) for every rule this app registers, filled with the slug
    and number of a project and experiment that really exist -- so a 404 can
    only have come from the guard, not from a lookup that found nothing."""
    routes = []
    with app.test_request_context():
        for rule in app.url_map.iter_rules():
            if not rule.endpoint.startswith("lablog."):
                continue
            url = url_for(
                rule.endpoint, **{k: v for k, v in SAMPLE_ARGS.items() if k in rule.arguments}
            )
            for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
                routes.append((method, url))
    return routes


@pytest.fixture
def populated(app: Flask, holder: FlaskClient) -> Flask:
    slug = make_project(holder)
    assert slug == SAMPLE_ARGS["slug"]
    start_experiment(holder, slug)
    return app


def test_the_sweep_covers_every_view(populated: Flask) -> None:
    """Guard against the sweep itself shrinking to nothing and passing."""
    assert len(_routes(populated)) == 12


def test_a_visitor_who_is_not_signed_in_is_sent_to_log_in(populated: Flask) -> None:
    client = populated.test_client()
    for method, url in _routes(populated):
        response = client.open(url, method=method)
        assert response.status_code == 302, (method, url)
        assert response.headers["Location"].startswith("/login"), (method, url)


def test_a_signed_in_stranger_gets_404_everywhere(populated: Flask, stranger: FlaskClient) -> None:
    for method, url in _routes(populated):
        assert stranger.open(url, method=method).status_code == 404, (method, url)


def test_the_role_holder_reaches_every_page(populated: Flask, holder: FlaskClient) -> None:
    for method, url in _routes(populated):
        if method == "GET":
            assert holder.get(url).status_code == 200, url


def test_a_stranger_changes_nothing(populated: Flask, stranger: FlaskClient) -> None:
    """A 404 that still did the work would be no guard at all."""
    stranger.post("/lablog/new", data={"name": "Intruder"})
    add_entry(stranger, "podpack-pdf", 1, body="written by a stranger")
    with populated.app_context():
        assert [p.name for p in db.session.scalars(db.select(Project))] == ["Podpack PDF"]
        assert db.session.scalars(db.select(Project)).one().experiments[0].entries == []


def test_forms_refuse_a_post_without_a_csrf_token(app: Flask, holder: FlaskClient) -> None:
    """podpack installs no CSRFProtect; FlaskForm's token is the protection.

    Turned on after signing in, because flask-security's login form would
    otherwise want a token from the test client too.
    """
    app.config["WTF_CSRF_ENABLED"] = True
    response = holder.post("/lablog/new", data={"name": "Forged"})
    assert response.status_code == 200
    assert "CSRF token is missing" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalars(db.select(Project)).all() == []
