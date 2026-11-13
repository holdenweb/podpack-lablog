"""The record itself: projects, experiments, entries, and the locks.

Driven through the pages for the behaviour a user sees, and through the model
for the rules, which have to hold whatever route reaches them. Every refusal is
checked twice -- that it answered 409, and that nothing changed -- because a
409 that still wrote the change would read as working.
"""

import re
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from conftest import HOLDER, add_entry, conclude, make_project, start_experiment

from podpack import User, db
from podpack_lablog.models import Entry, Experiment, Locked, Project


def _experiment(app: Flask, slug: str = "podpack-pdf", number: int = 1) -> Experiment:
    project = db.session.scalars(db.select(Project).where(Project.slug == slug)).one()
    return next(e for e in project.experiments if e.number == number)


def _snapshot(app: Flask, slug: str = "podpack-pdf", number: int = 1) -> tuple:
    """Everything a refusal must leave alone."""
    with app.app_context():
        e = _experiment(app, slug, number)
        return (
            e.expectation, e.concluded, e.outcome, e.conclusion,
            [(x.kind, x.body, x.corrects_id) for x in e.entries],
        )


# -- projects -----------------------------------------------------------------


def test_a_project_is_created_and_listed(holder: FlaskClient) -> None:
    slug = make_project(holder, "Podpack PDF", "The *booklet* tools.")
    assert slug == "podpack-pdf"
    assert "Podpack PDF" in holder.get("/lablog/").get_data(as_text=True)
    body = holder.get(f"/lablog/{slug}/").get_data(as_text=True)
    assert "<em>booklet</em>" in body


def test_a_name_that_clashes_is_refused_beside_the_field(app: Flask, holder: FlaskClient) -> None:
    make_project(holder, "Podpack PDF")
    response = holder.post("/lablog/new", data={"name": "podpack  pdf"})  # same slug
    assert response.status_code == 200
    assert "already exists" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Project.id))) == 1


@pytest.mark.parametrize("name, complaint", [("New", "own pages"), ("!!!", "letter or digit")])
def test_names_that_cannot_make_an_address_are_refused(
    holder: FlaskClient, name: str, complaint: str
) -> None:
    response = holder.post("/lablog/new", data={"name": name})
    assert response.status_code == 200
    assert complaint in response.get_data(as_text=True)


def test_a_rename_keeps_the_address(holder: FlaskClient) -> None:
    slug = make_project(holder, "Podpack PDF")
    response = holder.post(f"/lablog/{slug}/edit", data={"name": "PDF tools", "description": ""})
    assert response.status_code == 302
    body = holder.get(f"/lablog/{slug}/").get_data(as_text=True)
    assert "<h2>PDF tools</h2>" in body


def test_an_unknown_project_or_experiment_is_404(holder: FlaskClient) -> None:
    slug = make_project(holder)
    assert holder.get("/lablog/no-such-project/").status_code == 404
    assert holder.get(f"/lablog/{slug}/experiments/9").status_code == 404


# -- experiments --------------------------------------------------------------


def test_experiments_are_numbered_within_their_project(holder: FlaskClient) -> None:
    pdf, qr = make_project(holder, "PDF"), make_project(holder, "QR code")
    assert [start_experiment(holder, pdf), start_experiment(holder, pdf)] == [1, 2]
    assert start_experiment(holder, qr) == 1


def test_an_experiment_needs_an_expectation(app: Flask, holder: FlaskClient) -> None:
    slug = make_project(holder)
    response = holder.post(
        f"/lablog/{slug}/experiments/new", data={"title": "No idea", "expectation": "   "}
    )
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Experiment.id))) == 0


def test_the_expectation_can_be_revised_until_something_is_recorded(
    app: Flask, holder: FlaskClient
) -> None:
    slug = make_project(holder)
    start_experiment(holder, slug, expectation="Twelve pages.")
    response = holder.post(
        f"/lablog/{slug}/experiments/1/expectation", data={"expectation": "Twelve pages, in order."}
    )
    assert response.status_code == 302
    assert _snapshot(app)[0] == "Twelve pages, in order."


def test_the_first_entry_fixes_the_expectation(app: Flask, holder: FlaskClient) -> None:
    slug = make_project(holder)
    start_experiment(holder, slug, expectation="Twelve pages.")
    add_entry(holder, slug, 1)
    before = _snapshot(app)
    response = holder.post(
        f"/lablog/{slug}/experiments/1/expectation", data={"expectation": "Eleven, obviously."}
    )
    assert response.status_code == 409
    assert "expectation is fixed" in response.get_data(as_text=True)
    assert _snapshot(app) == before


def test_a_pasted_claude_output_counts_as_recorded(app: Flask, holder: FlaskClient) -> None:
    slug = make_project(holder)
    start_experiment(holder, slug)
    add_entry(holder, slug, 1, body="Claude said so.", kind="claude")
    response = holder.post(
        f"/lablog/{slug}/experiments/1/expectation", data={"expectation": "Changed."}
    )
    assert response.status_code == 409


def test_entries_are_appended_and_labelled_by_kind(holder: FlaskClient) -> None:
    slug = make_project(holder)
    start_experiment(holder, slug)
    assert add_entry(holder, slug, 1, body="Seen with my own eyes.").status_code == 302
    assert add_entry(holder, slug, 1, body="Reported by Claude.", kind="claude").status_code == 302
    body = holder.get(f"/lablog/{slug}/experiments/1").get_data(as_text=True)
    assert body.index("#1 Observation") < body.index("Seen with my own eyes.")
    assert body.index("#2 Claude output") < body.index("Reported by Claude.")
    assert body.index("Seen with my own eyes.") < body.index("Reported by Claude.")
    assert HOLDER in body


def test_a_correction_names_the_entry_it_corrects(app: Flask, holder: FlaskClient) -> None:
    slug = make_project(holder)
    start_experiment(holder, slug)
    add_entry(holder, slug, 1, body="Eleven pages.")
    with app.app_context():
        first = _experiment(app).entries[0].id
    add_entry(holder, slug, 1, body="Twelve: I miscounted.", corrects=first)
    body = holder.get(f"/lablog/{slug}/experiments/1").get_data(as_text=True)
    assert re.search(rf'corrects <a href="#entry-{first}">#1</a>', body)
    assert "corrected by" in body
    with app.app_context():
        assert _experiment(app).entries[1].corrects_id == first


def test_a_correction_cannot_point_into_another_experiment(app: Flask, holder: FlaskClient) -> None:
    slug = make_project(holder)
    start_experiment(holder, slug)
    start_experiment(holder, slug)
    add_entry(holder, slug, 1, body="In experiment one.")
    with app.app_context():
        foreign = _experiment(app, number=1).entries[0].id
    response = add_entry(holder, slug, 2, body="Sneaky.", corrects=foreign)
    assert response.status_code == 200  # the form refuses the choice
    assert "Not a valid choice" in response.get_data(as_text=True)
    assert _snapshot(app, number=2)[4] == []


def test_an_experiment_with_nothing_recorded_cannot_be_concluded(
    app: Flask, holder: FlaskClient
) -> None:
    slug = make_project(holder)
    start_experiment(holder, slug)
    before = _snapshot(app)
    response = conclude(holder, slug, 1)
    assert response.status_code == 409
    assert "nothing to conclude from" in response.get_data(as_text=True)
    assert _snapshot(app) == before


def test_concluding_closes_the_record(app: Flask, holder: FlaskClient) -> None:
    slug = make_project(holder)
    start_experiment(holder, slug)
    add_entry(holder, slug, 1)
    assert conclude(holder, slug, 1, outcome="not_as_expected").status_code == 302
    before = _snapshot(app)
    assert before[2] == "not_as_expected"

    assert add_entry(holder, slug, 1, body="An afterthought.").status_code == 409
    assert holder.post(
        f"/lablog/{slug}/experiments/1/expectation", data={"expectation": "x"}
    ).status_code == 409
    assert conclude(holder, slug, 1, outcome="as_expected").status_code == 409
    assert _snapshot(app) == before

    body = holder.get(f"/lablog/{slug}/experiments/1").get_data(as_text=True)
    assert "Conclusion: Not as expected" in body
    assert "this record is closed" in body
    assert 'name="body"' not in body  # no entry form is offered


def test_the_complete_log_shows_everything_in_order(holder: FlaskClient) -> None:
    slug = make_project(holder)
    start_experiment(holder, slug, title="First run", expectation="EXPECT-ONE")
    add_entry(holder, slug, 1, body="OBSERVE-ONE")
    conclude(holder, slug, 1, text="CONCLUDE-ONE")
    start_experiment(holder, slug, title="Second run", expectation="EXPECT-TWO")
    add_entry(holder, slug, 2, body="OBSERVE-TWO", kind="claude")
    body = holder.get(f"/lablog/{slug}/").get_data(as_text=True)
    markers = ["EXPECT-ONE", "OBSERVE-ONE", "CONCLUDE-ONE", "EXPECT-TWO", "OBSERVE-TWO"]
    positions = [body.index(m) for m in markers]
    assert positions == sorted(positions)
    assert 'href="#experiment-2">experiment 2</a>' in body  # listed as open


# -- the rules, below the views -------------------------------------------------


@pytest.fixture
def bare(app: Flask, holder: FlaskClient) -> Iterator[Experiment]:
    """An experiment reached through the model, in a pushed app context."""
    slug = make_project(holder)
    start_experiment(holder, slug)
    context = app.app_context()
    context.push()
    yield _experiment(app)
    db.session.rollback()
    context.pop()


def _author() -> User:
    return db.session.scalars(db.select(User).where(User.email == HOLDER)).one()


def test_the_model_fixes_the_expectation(bare: Experiment) -> None:
    bare.add_entry("observation", "seen", _author())
    with pytest.raises(Locked):
        bare.revise_expectation("hindsight")


def test_the_model_closes_a_concluded_experiment(bare: Experiment) -> None:
    bare.add_entry("observation", "seen", _author())
    bare.conclude("inconclusive", "unclear", _author())
    with pytest.raises(Locked):
        bare.add_entry("observation", "late", _author())
    with pytest.raises(Locked):
        bare.conclude("as_expected", "again", _author())


def test_the_model_refuses_a_conclusion_from_nothing(bare: Experiment) -> None:
    with pytest.raises(Locked):
        bare.conclude("as_expected", "assumed", _author())


def test_the_model_refuses_a_correction_from_elsewhere(app: Flask, bare: Experiment) -> None:
    other = bare.project.start_experiment("other", "other", _author())
    stray = other.add_entry("observation", "elsewhere", _author())
    with pytest.raises(ValueError, match="same experiment"):
        bare.add_entry("observation", "fix", _author(), corrects=stray)


def test_the_model_refuses_an_unknown_kind(bare: Experiment) -> None:
    with pytest.raises(ValueError, match="kind"):
        bare.add_entry("guess", "x", _author())


def test_the_model_refuses_an_unknown_outcome(bare: Experiment) -> None:
    entry = bare.add_entry("observation", "x", _author())
    assert isinstance(entry, Entry)
    with pytest.raises(ValueError, match="outcome"):
        bare.conclude("maybe", "x", _author())
