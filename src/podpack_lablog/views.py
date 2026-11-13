"""The lab log's pages.

Every view is behind `role_required`. A signed-out visitor is sent to log in,
which is flask-security's own answer and lets a bookmark work from a cold
browser; a signed-in user without the role gets a 404, as podpack's
`operator_required` gives, because the site this was built for allows public
sign-up and a confirmed stranger must reach nothing an anonymous visitor
cannot.

The record's rules are the model's (see models.py). What the views add is the
order of judgement: a request the rules refuse gets a 409 and the page with the
reason on it, before its form is even looked at. A form that fails validation
comes back with 200 and its own complaints, as podpack-notes and flask-security
both answer one.

Nothing here uses `flash()`. The refusal is rendered into the page itself,
because a site whose chrome never calls `get_flashed_messages()` would swallow
it -- writing-an-app.md measured exactly that.
"""

import functools
import textwrap
from collections.abc import Callable
from logging import getLogger
from typing import Any, ParamSpec, TypeVar

import sqlalchemy as sa
from flask import Blueprint, abort, current_app, redirect, render_template, url_for
from flask.typing import ResponseReturnValue
from flask_security import auth_required, current_user
from wtforms.fields.choices import SelectChoice

from podpack import User, db

from .forms import (
    NOT_A_CORRECTION,
    ConclusionForm,
    EntryForm,
    ExpectationForm,
    ExperimentForm,
    ProjectForm,
)
from .models import OUTCOMES, RESERVED_SLUGS, Entry, Experiment, Locked, Project, slugify
from .render import markdown, stamp

logger = getLogger(__name__)

blueprint = Blueprint("lablog", __name__, template_folder="templates")

ROLE = "lablog"
"""The role that admits a signed-in user. Named after the app, so a site's
role list says what each role is for. Create and grant it with flask-security's
own commands: `flask roles create lablog`, `flask roles add <email> lablog`."""

_P = ParamSpec("_P")
_R = TypeVar("_R")


def role_required(view: Callable[_P, _R]) -> Callable[_P, _R]:
    """Signed-in holders of `ROLE` only.

    `auth_required("session")` runs first and answers the signed-out case its
    own way. Session only, not token: this app has no API, so a token could
    only ever be a second way in to the same pages.
    """

    @functools.wraps(view)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if not current_user.has_role(ROLE):
            abort(404)
        return view(*args, **kwargs)

    return auth_required("session")(guarded)  # type: ignore[no-any-return]


@blueprint.context_processor
def _helpers() -> dict[str, Any]:
    """Names this app's templates use, and only this app's.

    A blueprint context processor reaches the blueprint's own templates and no
    others, so nothing here can collide with another app's helpers -- or shadow
    `site` and `sections`, which the chrome needs.
    """
    zone = current_app.config["LABLOG_TIMEZONE"]
    return {
        "markdown": markdown,
        "stamp": lambda moment: stamp(moment, zone),
        "outcomes": OUTCOMES,
    }


def _text(field: Any) -> str:
    """A validated text field's value, stripped.

    `DataRequired` has already refused an empty or all-space value by the time
    this is called; the `or ""` is for an Optional field, and for the type
    checker, which cannot know a validator ran.
    """
    return str(field.data or "").strip()


def _me() -> User:
    """The signed-in user as a model instance, for relationships to hold.

    `current_user` is a proxy, and SQLAlchemy wants the object behind it.
    """
    return current_user._get_current_object()  # type: ignore[no-any-return]


# -- projects ---------------------------------------------------------------


@blueprint.route("/")
@role_required
def index() -> ResponseReturnValue:
    projects = db.session.scalars(sa.select(Project).order_by(Project.name)).all()
    return render_template("lablog/index.html", title="Lab log", projects=projects)


@blueprint.route("/new", methods=["GET", "POST"])
@role_required
def new_project() -> ResponseReturnValue:
    form = ProjectForm()
    if form.validate_on_submit():
        name = _text(form.name)
        slug = slugify(name)
        problem = _name_problem(name, slug)
        if problem is None:
            project = Project(
                name=name,
                slug=slug,
                description=_text(form.description),
                created_by=_me(),
            )
            db.session.add(project)
            db.session.commit()
            logger.info("project %r created by user %s", slug, current_user.id)
            return redirect(url_for("lablog.project", slug=slug))
        form.name.errors = [*form.name.errors, problem]
    return render_template(
        "lablog/project_form.html", title="New project", form=form, project=None
    )


@blueprint.route("/<slug>/")
@role_required
def project(slug: str) -> ResponseReturnValue:
    """The complete log: every experiment, in full, in the order they ran."""
    project = _project_or_404(slug)
    return render_template("lablog/project.html", title=project.name, project=project)


@blueprint.route("/<slug>/edit", methods=["GET", "POST"])
@role_required
def edit_project(slug: str) -> ResponseReturnValue:
    """Rename or re-describe. The slug stays, so every link to it still works."""
    project = _project_or_404(slug)
    form = ProjectForm(obj=project)
    if form.validate_on_submit():
        name = _text(form.name)
        problem = _name_problem(name, None, project)
        if problem is None:
            project.name = name
            project.description = _text(form.description)
            db.session.commit()
            return redirect(url_for("lablog.project", slug=slug))
        form.name.errors = [*form.name.errors, problem]
    return render_template(
        "lablog/project_form.html", title=f"Edit {project.name}", form=form, project=project
    )


def _name_problem(name: str, slug: str | None, current: Project | None = None) -> str | None:
    """Why `name` cannot be used, or None if it can.

    Checked here rather than left to the unique constraints, which would answer
    with an IntegrityError and a 500 instead of a sentence beside the field.
    `slug` is None on a rename, which keeps the old one.
    """
    if slug is not None:
        if not slug:
            return "A name needs at least one letter or digit."
        if slug in RESERVED_SLUGS:
            return "That name would collide with one of this app's own pages."
    clash = sa.or_(Project.name == name, Project.slug == slug) if slug else Project.name == name
    query = sa.select(Project.id).where(clash)
    if current is not None:
        query = query.where(Project.id != current.id)
    if db.session.scalar(query) is not None:
        return "A project with that name, or one giving the same address, already exists."
    return None


def _project_or_404(slug: str) -> Project:
    project = db.session.scalar(sa.select(Project).where(Project.slug == slug))
    if project is None:
        abort(404)
    return project


# -- experiments ------------------------------------------------------------


@blueprint.route("/<slug>/experiments/new", methods=["GET", "POST"])
@role_required
def new_experiment(slug: str) -> ResponseReturnValue:
    project = _project_or_404(slug)
    form = ExperimentForm()
    if form.validate_on_submit():
        experiment = project.start_experiment(
            _text(form.title), _text(form.expectation), _me()
        )
        db.session.commit()
        logger.info(
            "experiment %s/%d started by user %s", slug, experiment.number, current_user.id
        )
        return redirect(
            url_for("lablog.experiment", slug=slug, number=experiment.number)
        )
    return render_template(
        "lablog/experiment_form.html",
        title=f"New experiment on {project.name}",
        form=form,
        project=project,
    )


@blueprint.route("/<slug>/experiments/<int:number>")
@role_required
def experiment(slug: str, number: int) -> ResponseReturnValue:
    return _experiment_page(_experiment_or_404(slug, number))


@blueprint.route("/<slug>/experiments/<int:number>/expectation", methods=["POST"])
@role_required
def revise_expectation(slug: str, number: int) -> ResponseReturnValue:
    experiment = _experiment_or_404(slug, number)
    try:
        experiment.ensure_revisable()
    except Locked as refusal:
        return _experiment_page(experiment, refusal=str(refusal))
    form = ExpectationForm()
    if not form.validate_on_submit():
        return _experiment_page(experiment, expectation_form=form)
    experiment.revise_expectation(_text(form.expectation))
    db.session.commit()
    return redirect(url_for("lablog.experiment", slug=slug, number=number))


@blueprint.route("/<slug>/experiments/<int:number>/entries", methods=["POST"])
@role_required
def add_entry(slug: str, number: int) -> ResponseReturnValue:
    experiment = _experiment_or_404(slug, number)
    try:
        experiment.ensure_open()
    except Locked as refusal:
        return _experiment_page(experiment, refusal=str(refusal))
    form = _offer_corrections(EntryForm(), experiment)
    if not form.validate_on_submit():
        return _experiment_page(experiment, entry_form=form)
    corrects = (
        None
        if form.corrects.data == NOT_A_CORRECTION
        else db.session.get(Entry, form.corrects.data)
    )
    entry = experiment.add_entry(_text(form.kind), _text(form.body), _me(), corrects)
    db.session.commit()
    logger.info(
        "%s entry added to %s/%d by user %s", entry.kind, slug, number, current_user.id
    )
    return redirect(
        url_for("lablog.experiment", slug=slug, number=number, _anchor=f"entry-{entry.id}")
    )


@blueprint.route("/<slug>/experiments/<int:number>/conclusion", methods=["POST"])
@role_required
def conclude(slug: str, number: int) -> ResponseReturnValue:
    experiment = _experiment_or_404(slug, number)
    try:
        experiment.ensure_concludable()
    except Locked as refusal:
        return _experiment_page(experiment, refusal=str(refusal))
    form = ConclusionForm()
    if not form.validate_on_submit():
        return _experiment_page(experiment, conclusion_form=form)
    experiment.conclude(_text(form.outcome), _text(form.conclusion), _me())
    db.session.commit()
    logger.info("experiment %s/%d concluded by user %s", slug, number, current_user.id)
    return redirect(url_for("lablog.experiment", slug=slug, number=number))


def _experiment_or_404(slug: str, number: int) -> Experiment:
    project = _project_or_404(slug)
    experiment = db.session.scalar(
        sa.select(Experiment).where(
            Experiment.project_id == project.id, Experiment.number == number
        )
    )
    if experiment is None:
        abort(404)
    return experiment


def _offer_corrections(form: EntryForm, experiment: Experiment) -> EntryForm:
    """Fill the "corrects" choices with this experiment's entries and no others.

    WTForms rejects a submitted value that is not among the choices, so this is
    also what stops a hand-made request pointing a correction elsewhere.
    """
    # String values: WTForms renders and compares them after `coerce=int`.
    form.corrects.choices = [SelectChoice(str(NOT_A_CORRECTION), "Nothing: a new entry")] + [
        SelectChoice(
            str(entry.id),
            f"#{entry.position} {entry.kind_label}: "
            + textwrap.shorten(entry.body, 60, placeholder="…"),
        )
        for entry in experiment.entries
    ]
    return form


def _experiment_page(
    experiment: Experiment,
    *,
    expectation_form: ExpectationForm | None = None,
    entry_form: EntryForm | None = None,
    conclusion_form: ConclusionForm | None = None,
    refusal: str | None = None,
) -> ResponseReturnValue:
    """One experiment, with whichever forms its state allows.

    The forms not being re-shown are built with `formdata=None`. Left to read
    the request, a fresh form would fill itself from a POST meant for another
    one -- the expectation box would come back empty after a rejected entry.
    """
    return render_template(
        "lablog/experiment.html",
        title=f"{experiment.project.name}: experiment {experiment.number}",
        project=experiment.project,
        experiment=experiment,
        expectation_form=expectation_form or ExpectationForm(formdata=None, obj=experiment),
        entry_form=entry_form or _offer_corrections(EntryForm(formdata=None), experiment),
        conclusion_form=conclusion_form or ConclusionForm(formdata=None),
        refusal=refusal,
    ), (409 if refusal else 200)
