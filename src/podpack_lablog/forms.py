"""This app's forms.

flask-wtf, as podpack-notes, podpack-pdf and podpack-qrcode use, and here for
the reason notes gives: every route is guarded by a session cookie and podpack
installs no `CSRFProtect`, so the token `hidden_tag()` carries is the only CSRF
protection there is. That holds exactly as long as every form is one of these.

`DataRequired` throughout, which counts a string of spaces as absent.
"""

from flask_wtf import FlaskForm
from wtforms import RadioField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length, Optional

from .models import HOME_DIRECTORY_MAX, KINDS, OUTCOMES

NOT_A_CORRECTION = 0


def choices(labels: dict[str, str]) -> list[SelectChoice]:
    """`SelectChoice`s rather than tuples, which WTForms 3.3 deprecates and
    3.4 will refuse."""
    return [SelectChoice(value, label) for value, label in labels.items()]


class ProjectForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Description (Markdown)", validators=[Optional()])
    # Only the length is checked here. Whether it is a usable path is the
    # model's rule (`normalise_home`), so it holds for every route.
    home_directory = StringField(
        "Home directory", validators=[Optional(), Length(max=HOME_DIRECTORY_MAX)]
    )
    submit = SubmitField("Save project")


class ExperimentForm(FlaskForm):
    title = StringField("Title", validators=[DataRequired(), Length(max=200)])
    expectation = TextAreaField(
        "What do you expect to happen? (Markdown)", validators=[DataRequired()]
    )
    submit = SubmitField("Start experiment")


class ExpectationForm(FlaskForm):
    expectation = TextAreaField("Expectation (Markdown)", validators=[DataRequired()])
    submit = SubmitField("Revise expectation")


class EntryForm(FlaskForm):
    kind = RadioField(
        "Kind", choices=choices(KINDS), default="observation", validators=[DataRequired()]
    )
    body = TextAreaField("Entry (Markdown)", validators=[DataRequired()])
    # Choices are filled per experiment by the view, so a correction can only
    # be offered against an entry that is actually in this experiment.
    corrects = SelectField("Corrects", coerce=int, default=NOT_A_CORRECTION)
    submit = SubmitField("Add entry")


class ConclusionForm(FlaskForm):
    outcome = RadioField("Outcome", choices=choices(OUTCOMES), validators=[DataRequired()])
    conclusion = TextAreaField("Conclusion (Markdown)", validators=[DataRequired()])
    submit = SubmitField("Conclude experiment")
