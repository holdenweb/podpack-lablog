"""The lab log's schema, and the rules that make it a record.

Three tables, prefixed with the app's name because `db.metadata` is the one
namespace podpack shares across every installed app: a project, the
experiments run against it, and the entries each experiment accumulates.

The rules live here rather than in the views. A record is only as good as the
weakest path that can rewrite it, and a rule enforced by a view lasts until the
next view is added; enforced by the model, a new route has to go out of its way
to break it. Each refusal raises `Locked`, which the views turn into a 409.

Nothing imports this module explicitly in production -- the registry imports it
while installing the app -- but the views do, so the tables would reach
`db.metadata` either way.
"""

import re
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, relationship

from podpack import User, db

OUTCOMES: dict[str, str] = {
    "as_expected": "As expected",
    "not_as_expected": "Not as expected",
    "inconclusive": "Inconclusive",
}
"""What a conclusion says about the expectation, in display order.

Stored as strings checked here rather than as a PostgreSQL ENUM: autogenerate
creates an ENUM's type but no downgrade ever drops it, and adding a value means
an `ALTER TYPE` that no autogenerate will write."""

KINDS: dict[str, str] = {
    "observation": "Observation",
    "claude": "Claude output",
}
"""What an entry is. Claude's output is kept distinct from what a person saw,
because the two carry different weight as evidence."""

RESERVED_SLUGS = frozenset({"new"})
"""Slugs that would read as one of this app's own routes."""


class Locked(Exception):
    """A change the record's rules refuse: the message says which rule."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(name: str) -> str:
    """The URL segment a project's name becomes.

    Computed once, when the project is created, and never again: renaming a
    project must not break every bookmark and every link pasted into an entry.
    """
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _user_fk(table: str, column: str, nullable: bool = False) -> sa.Column:
    """A column referring to podpack's `user` table.

    RESTRICT, as podpack-notes chose: deleting a user who wrote part of the
    record should fail and say so rather than take their entries with them.
    Named, because `db.metadata` carries no naming convention and an anonymous
    constraint autogenerates `op.drop_constraint(None, ...)`, which no site can
    run.
    """
    return db.Column(
        db.Integer,
        db.ForeignKey(
            "user.id", ondelete="RESTRICT", name=f"fk_{table}_{column}_user"
        ),
        nullable=nullable,
    )


class Project(db.Model):  # type: ignore[name-defined]  # flask-sqlalchemy builds db.Model at runtime
    """Something being tested. Its name and description are not part of the
    locked record, so both stay editable; its slug does not change."""

    __tablename__ = "lablog_project"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    slug = db.Column(db.String(200), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=False, default="")
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    created_by_id = _user_fk("lablog_project", "created_by_id")

    # No backrefs onto podpack's User: bolting attributes onto the framework's
    # model from inside an app is not an app's to do.
    #
    # Relationships carry `Mapped[...]` and columns do not. Columns in the
    # classic style follow podpack-notes; relationships are what the code
    # iterates and navigates, and unannotated mypy types them as the
    # property object rather than what it loads. SQLAlchemy's `relationship`
    # rather than `db.relationship`: the same function, but flask-sqlalchemy
    # types its wrapper's result in a way `Mapped[...]` will not accept.
    created_by: Mapped[User] = relationship(User)
    experiments: Mapped[list["Experiment"]] = relationship(
        "Experiment", back_populates="project", order_by="Experiment.number"
    )

    @property
    def last_activity(self) -> datetime:
        """The latest moment anything was recorded here, for the index page."""
        moments = [self.created]
        for experiment in self.experiments:
            moments.append(experiment.created)
            moments.extend(entry.created for entry in experiment.entries)
            if experiment.concluded is not None:
                moments.append(experiment.concluded)
        # SQLite hands back naive datetimes and PostgreSQL aware ones; within
        # one database they agree, so comparing them is safe.
        return max(moments)

    @property
    def open_experiments(self) -> list["Experiment"]:
        return [e for e in self.experiments if not e.is_concluded]

    def start_experiment(self, title: str, expectation: str, author: User) -> "Experiment":
        """The next experiment, numbered within this project.

        max+1 rather than a sequence, so the numbers mean "the fourth thing we
        tried on this project" and never skip. The unique constraint on
        `(project_id, number)` is what stops two simultaneous starts agreeing
        on a number; at one tester's pace that is a backstop, not a hot path.
        """
        latest = db.session.scalar(
            sa.select(sa.func.max(Experiment.number)).where(
                Experiment.project_id == self.id
            )
        )
        experiment = Experiment(
            project=self,
            number=(latest or 0) + 1,
            title=title,
            expectation=expectation,
            created_by=author,
        )
        db.session.add(experiment)
        return experiment


class Experiment(db.Model):  # type: ignore[name-defined]
    """One test of a project: an expectation, then entries, then a conclusion.

    The expectation is written first and freezes as soon as anything has been
    observed, so it cannot be adjusted with hindsight. Concluding closes the
    experiment; a wrong conclusion is answered by a new experiment, not by an
    edit.
    """

    __tablename__ = "lablog_experiment"
    __table_args__ = (
        db.UniqueConstraint(
            "project_id", "number", name="uq_lablog_experiment_project_id_number"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "lablog_project.id",
            ondelete="RESTRICT",
            name="fk_lablog_experiment_project_id_lablog_project",
        ),
        nullable=False,
    )
    number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    expectation = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    created_by_id = _user_fk("lablog_experiment", "created_by_id")
    concluded = db.Column(db.DateTime(timezone=True), nullable=True)
    concluded_by_id = _user_fk("lablog_experiment", "concluded_by_id", nullable=True)
    outcome = db.Column(db.String(20), nullable=True)
    conclusion = db.Column(db.Text, nullable=True)

    project: Mapped[Project] = relationship(Project, back_populates="experiments")
    created_by: Mapped[User] = relationship(User, foreign_keys=[created_by_id])
    concluded_by: Mapped[User | None] = relationship(User, foreign_keys=[concluded_by_id])
    entries: Mapped[list["Entry"]] = relationship(
        "Entry", back_populates="experiment", order_by="Entry.id"
    )

    @property
    def is_concluded(self) -> bool:
        return self.concluded is not None

    @property
    def expectation_locked(self) -> bool:
        """Locked by the first entry, not by the first *observation* alone: a
        pasted Claude output is something seen too."""
        return bool(self.entries)

    @property
    def outcome_label(self) -> str:
        return OUTCOMES.get(self.outcome or "", "Open")

    # Each rule is a method of its own, called by the change it guards and also
    # by the view before it looks at the form -- so a refused request is refused
    # for the reason that matters, not for an empty field it also happened to
    # have.

    def ensure_revisable(self) -> None:
        if self.expectation_locked:
            raise Locked(
                "The expectation is fixed: something has already been recorded "
                "against it. Record a correction instead, or start a new experiment."
            )

    def ensure_open(self) -> None:
        if self.is_concluded:
            raise Locked("This experiment is concluded and its record is closed.")

    def ensure_concludable(self) -> None:
        if self.is_concluded:
            raise Locked("This experiment is already concluded.")
        if not self.entries:
            raise Locked(
                "Nothing has been recorded yet, so there is nothing to conclude from."
            )

    def revise_expectation(self, text: str) -> None:
        self.ensure_revisable()
        self.expectation = text

    def add_entry(
        self, kind: str, body: str, author: User, corrects: "Entry | None" = None
    ) -> "Entry":
        self.ensure_open()
        if kind not in KINDS:
            raise ValueError(f"unknown entry kind {kind!r}")
        if corrects is not None and corrects.experiment is not self:
            # The form offers only this experiment's entries, so arriving here
            # means a hand-made request; refusing it keeps a correction from
            # pointing into another experiment's record.
            raise ValueError("a correction must refer to an entry in the same experiment")
        entry = Entry(
            experiment=self, kind=kind, body=body, author=author, corrects=corrects
        )
        db.session.add(entry)
        return entry

    def conclude(self, outcome: str, text: str, author: User) -> None:
        self.ensure_concludable()
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}")
        self.outcome = outcome
        self.conclusion = text
        self.concluded_by = author
        self.concluded = _now()


class Entry(db.Model):  # type: ignore[name-defined]
    """One thing recorded during an experiment. Never edited: a mistake is
    answered by a later entry that names it in `corrects`."""

    __tablename__ = "lablog_entry"

    id = db.Column(db.Integer, primary_key=True)
    experiment_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "lablog_experiment.id",
            ondelete="RESTRICT",
            name="fk_lablog_entry_experiment_id_lablog_experiment",
        ),
        nullable=False,
    )
    kind = db.Column(db.String(20), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    author_id = _user_fk("lablog_entry", "author_id")
    corrects_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "lablog_entry.id",
            ondelete="RESTRICT",
            name="fk_lablog_entry_corrects_id_lablog_entry",
        ),
        nullable=True,
    )

    experiment: Mapped[Experiment] = relationship(Experiment, back_populates="entries")
    author: Mapped[User] = relationship(User)
    corrects: Mapped["Entry | None"] = relationship(
        "Entry", remote_side=[id], back_populates="corrections"
    )
    corrections: Mapped[list["Entry"]] = relationship(
        "Entry", back_populates="corrects", order_by="Entry.id"
    )

    @property
    def kind_label(self) -> str:
        return KINDS[self.kind]

    @property
    def position(self) -> int:
        """Where this entry sits in its experiment, counting from 1 -- the "#3"
        a reader refers to. Derived rather than stored, since entries are only
        ever appended and so never move."""
        return self.experiment.entries.index(self) + 1
