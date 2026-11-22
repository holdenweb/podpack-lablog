"""A project's home directory: optional, absolute, one string per directory,
and never shared.

It exists so that a client working in a directory can find "the project for
this directory", and that lookup compares text. So the tests that matter most
are the ones about equal directories arriving as equal strings, and about two
projects never claiming the same one. As in test_record.py, the rules are
checked through the model as well as the pages, and every refusal twice: that
it was refused, and that nothing changed.
"""

from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.exc import IntegrityError
from werkzeug.test import TestResponse

from conftest import HOLDER, make_project

from podpack import User, db
from podpack_lablog.models import Project, normalise_home


def _create(client: FlaskClient, name: str, home: str) -> TestResponse:
    return client.post("/lablog/new", data={"name": name, "description": "", "home_directory": home})


def _edit(client: FlaskClient, slug: str, name: str, home: str) -> TestResponse:
    return client.post(
        f"/lablog/{slug}/edit", data={"name": name, "description": "", "home_directory": home}
    )


def _homes(app: Flask) -> dict[str, str | None]:
    with app.app_context():
        return {p.slug: p.home_directory for p in db.session.scalars(db.select(Project))}


# -- the rule itself ------------------------------------------------------------


@pytest.mark.parametrize(
    "given, stored",
    [
        ("/Users/me/sites/podpack-pdf", "/Users/me/sites/podpack-pdf"),
        ("/Users/me/sites/podpack-pdf/", "/Users/me/sites/podpack-pdf"),
        ("  /Users/me/sites/podpack-pdf  ", "/Users/me/sites/podpack-pdf"),
        ("/Users/me//sites/podpack-pdf", "/Users/me/sites/podpack-pdf"),
        ("/Users/me/./sites/podpack-pdf", "/Users/me/sites/podpack-pdf"),
        ("/Users/me/tmp/../sites/podpack-pdf", "/Users/me/sites/podpack-pdf"),
        ("//Users/me/sites/podpack-pdf", "/Users/me/sites/podpack-pdf"),
        ("/", "/"),
    ],
)
def test_one_directory_is_stored_as_one_string(given: str, stored: str) -> None:
    assert normalise_home(given) == stored


@pytest.mark.parametrize("given", [None, "", "   "])
def test_no_home_is_none_not_an_empty_string(given: str | None) -> None:
    """None, because the unique constraint lets NULLs coexist and would not
    let two empty strings."""
    assert normalise_home(given) is None


@pytest.mark.parametrize(
    "given, complaint",
    [
        ("~/sites/podpack-pdf", "whose home ~ means"),
        ("~", "whose home ~ means"),
        ("sites/podpack-pdf", "absolute path"),
        ("./podpack-pdf", "absolute path"),
    ],
)
def test_a_path_that_names_no_one_directory_is_refused(given: str, complaint: str) -> None:
    with pytest.raises(ValueError, match=complaint):
        normalise_home(given)


@pytest.fixture
def context(app: Flask) -> Iterator[None]:
    """A pushed app context, for reaching the model directly."""
    pushed = app.app_context()
    pushed.push()
    yield
    db.session.rollback()
    pushed.pop()


def test_the_model_normalises_whatever_route_sets_it(context: None) -> None:
    project = Project(name="Direct", slug="direct", home_directory="/Users/me/x/")
    assert project.home_directory == "/Users/me/x"
    with pytest.raises(ValueError):
        project.home_directory = "relative/x"


def test_the_database_refuses_a_shared_home(app: Flask, holder: FlaskClient, context: None) -> None:
    """The constraint, under the views' friendlier check: a route that forgot
    to check still cannot store a clash. `holder` signs in first so the user
    the projects are created by exists."""
    author = db.session.scalars(db.select(User).where(User.email == HOLDER)).one()
    db.session.add(Project(name="One", slug="one", home_directory="/x", created_by=author))
    db.session.commit()
    db.session.add(Project(name="Two", slug="two", home_directory="/x", created_by=author))
    with pytest.raises(IntegrityError):
        db.session.commit()


# -- through the pages -------------------------------------------------------------


def test_a_project_is_created_with_its_home(app: Flask, holder: FlaskClient) -> None:
    response = _create(holder, "Podpack PDF", "/Users/me/sites/podpack-pdf/")
    assert response.status_code == 302
    assert _homes(app) == {"podpack-pdf": "/Users/me/sites/podpack-pdf"}
    assert "<code>/Users/me/sites/podpack-pdf</code>" in holder.get("/lablog/podpack-pdf/").get_data(as_text=True)
    assert "<code>/Users/me/sites/podpack-pdf</code>" in holder.get("/lablog/").get_data(as_text=True)


def test_projects_without_a_home_do_not_clash(app: Flask, holder: FlaskClient) -> None:
    make_project(holder, "Podpack PDF")
    make_project(holder, "Podpack Notes")
    assert _homes(app) == {"podpack-pdf": None, "podpack-notes": None}


@pytest.mark.parametrize(
    "home, complaint", [("~/sites/x", "whose home ~ means"), ("sites/x", "absolute path")]
)
def test_an_unusable_home_is_refused_beside_the_field(
    app: Flask, holder: FlaskClient, home: str, complaint: str
) -> None:
    response = _create(holder, "Podpack PDF", home)
    assert response.status_code == 200
    assert complaint in response.get_data(as_text=True)
    assert _homes(app) == {}


def test_a_home_already_claimed_is_refused_however_it_is_spelt(app: Flask, holder: FlaskClient) -> None:
    _create(holder, "Podpack PDF", "/Users/me/sites/podpack-pdf")
    response = _create(holder, "Podpack Notes", "/Users/me/sites//podpack-pdf/")
    assert response.status_code == 200
    assert "already the home directory of Podpack PDF" in response.get_data(as_text=True)
    assert _homes(app) == {"podpack-pdf": "/Users/me/sites/podpack-pdf"}


def test_a_home_can_be_set_moved_and_cleared(app: Flask, holder: FlaskClient) -> None:
    slug = make_project(holder, "Podpack PDF")
    for home, stored in [("/a/pdf", "/a/pdf"), ("/b/pdf/", "/b/pdf"), ("", None)]:
        assert _edit(holder, slug, "Podpack PDF", home).status_code == 302
        assert _homes(app)[slug] == stored


def test_saving_a_project_does_not_clash_with_its_own_home(holder: FlaskClient) -> None:
    _create(holder, "Podpack PDF", "/a/pdf")
    assert _edit(holder, "podpack-pdf", "PDF tools", "/a/pdf/").status_code == 302


def test_moving_onto_another_projects_home_is_refused(app: Flask, holder: FlaskClient) -> None:
    _create(holder, "Podpack PDF", "/a/pdf")
    _create(holder, "Podpack Notes", "/a/notes")
    response = _edit(holder, "podpack-notes", "Podpack Notes", "/a/pdf")
    assert response.status_code == 200
    assert "already the home directory of Podpack PDF" in response.get_data(as_text=True)
    assert _homes(app) == {"podpack-pdf": "/a/pdf", "podpack-notes": "/a/notes"}


def test_an_edit_that_omits_the_field_keeps_the_home(app: Flask, holder: FlaskClient) -> None:
    """A client posting only name and description, as every edit did before
    this field existed, must not wipe the home directory by leaving it out."""
    _create(holder, "Podpack PDF", "/a/pdf")
    holder.post("/lablog/podpack-pdf/edit", data={"name": "PDF tools", "description": ""})
    assert _homes(app) == {"podpack-pdf": "/a/pdf"}
