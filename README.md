# podpack-lablog

A testing history for each of your projects, as a [podpack](https://github.com/holdenweb/podpack) app.

Each test is an **experiment**. You start one by saying what you expect to
happen. Then you record what you observe, including output pasted from Claude,
which is kept marked as Claude's. You finish with a conclusion and an outcome.
A project's page is its complete log: every experiment in full, oldest first.

## The rules of the record

The log is a record, so it can't be rewritten with hindsight:

- **The expectation is fixed by the first entry.** You can revise it until
  something has been recorded against it, and not after.
- **Entries are never edited.** A mistake is answered by a later entry marked
  as a correction, and the two link to each other ("corrects #2",
  "corrected by #3").
- **Concluding closes the experiment.** An experiment needs at least one entry
  before it can be concluded. If a conclusion later turns out wrong, start a
  new experiment.

The model enforces these rules, not the pages, so a route added later can't
bypass them. A refused request gets **409** with the reason on the page, and
nothing changes. A project's name and description aren't part of the record,
so they stay editable. Its address doesn't change.

Entries, expectations and conclusions are Markdown (CommonMark, plus tables and
strikethrough). Raw HTML is shown as text rather than run, and `javascript:`
links aren't made live. `render.py` says why this is markdown-it-py and not the
`markdown` library podpack-pages uses.

## Who can use it

Signed-in users who hold the **`lablog` role**:

| Caller | Gets |
| --- | --- |
| not signed in | redirected to `/login`, and back afterwards |
| signed in, without the role | **404** on every page, as podpack's `operator_required` gives |
| signed in, with the role | everything |

The 404 is deliberate. On a site with public sign-up, a confirmed stranger must
reach nothing an anonymous visitor can't.

**There is no nav entry.** A podpack `Section` renders for every visitor, so on
a public site an entry would advertise a page most people can only get a 404
from. Bookmark `/lablog/`, or link to it from your chrome for those who hold
the role.

## Installing it into a site

Requires **podpack 0.9.0 or later**, the first release with `Backup`. It was
developed and tested against 0.10.1. Nothing enforces the minimum, because an
app can't name podpack in its dependencies.

1. Add the distribution. This needs a rebuild to reach the image:

   ```bash
   uv add podpack-lablog
   ```

2. Enable it in the site's `config/app.toml`:

   ```toml
   [site]
   apps = [..., "podpack_lablog"]      # the IMPORT name

   [apps.lablog]                       # the APP's name
   timezone = "Europe/London"          # optional; how times are shown. Default "UTC"
   ```

   An unknown zone stops the site booting, rather than breaking the first page
   that shows a time.

3. Generate and apply the schema. Apps can't ship migrations, so each site
   generates the app's three tables (`lablog_project`, `lablog_experiment`,
   `lablog_entry`) in its own alembic history. Run the site's usual
   autogenerate **with its full app list enabled**: with an app missing,
   alembic proposes dropping that app's tables. Check that the revision creates
   only `lablog_*` tables and drops nothing.

4. Create the role and grant it:

   ```bash
   flask --app <site> roles create lablog
   ```

   ```bash
   flask --app <site> roles add <email> lablog
   ```

   flask-security's `roles create` prints *created successfully* and then fails
   with an `IntegrityError` traceback if the role already exists. The role is
   there either way.

Until the role exists and somebody holds it, every page answers 404 to
everyone. `/_status` reports this under `apps.lablog.reported`. The output
below is from the dev site:

```json
"reported": {"holders": 1, "role": "lablog", "role_exists": true}
```

Three spellings of one thing:

| | |
| --- | --- |
| `podpack-lablog` | the distribution, which is what a site depends on |
| `podpack_lablog` | the import name, which goes in `[site] apps` |
| `lablog` | the app's name, from its blueprint: `[apps.lablog]`, `[site.mounts]`, `templates/lablog/`, the role, the table prefix, `lablog.log` |

## What it gives the site

Mounted at `/lablog` unless the site says otherwise in `[site.mounts]`.

| Route | |
| --- | --- |
| `GET /` | projects, with their experiment counts, open experiments and last activity |
| `GET, POST /new` | add a project |
| `GET /<slug>/` | the complete log |
| `GET, POST /<slug>/edit` | rename a project or edit its description |
| `GET, POST /<slug>/experiments/new` | start an experiment |
| `GET /<slug>/experiments/<n>` | one experiment, with the forms its state allows |
| `POST /<slug>/experiments/<n>/expectation` | revise the expectation |
| `POST /<slug>/experiments/<n>/entries` | record an observation or Claude output, optionally as a correction |
| `POST /<slug>/experiments/<n>/conclusion` | record the outcome and conclusion |

All forms carry a CSRF token (flask-wtf). podpack installs no site-wide CSRF
protection, so that token is the protection. Refusals are rendered into the
page itself rather than flashed, so they show on a site whose chrome never
calls `get_flashed_messages()`.

Everything is stored in the database. The app writes nothing to its data
directory and declares `Backup(data=False)`, so a PostgreSQL dump is the whole
backup.

## Upgrading a site to a new version

Release notes will say when a version changes the schema. When one does,
autogenerate and apply a revision on the site as in step 3 above. Nothing else
will remind you.

## Developing it

```bash
uv sync --all-groups
```

```bash
uv run pytest
```

```bash
uv run mypy
```

podpack comes from its git repository through the dev group, so this works on
any machine. To change the framework and this app together, switch to a sibling
checkout:

```bash
uv add --dev --editable ../podpack
```

To run it by hand, `devsite.py` builds a throwaway site with this app
installed, on SQLite, in podpack's default chrome. `seed` creates
holder@example.com, who holds the `lablog` and `admin` roles, and
stranger@example.com, who holds neither. The password is `DEV_PASSWORD` in
`devsite.py`. `admin` lets the holder read `/_status`.

```bash
uv run flask --app devsite seed
```

```bash
uv run flask --app devsite run -p 5001
```

Until `seed` has run, podpack warns at boot that no `admin` role exists, and
tells you to run `flask --app podpack roles create admin`. **Don't run that
here.** podpack takes the name from the Flask app, which is `podpack` for a
site with no package of its own, and `--app podpack` then looks for the
container's `/etc/podpack/app.toml` and fails. Run `seed` instead. On this
site, every `flask` command is `--app devsite`.

The dev site reads templates once, so restart it after changing one.

## Releasing

Bump the version, commit, tag `r<version>` and push the tag.
`.github/workflows/publish.yml` checks that the tag matches the version, runs
the tests, builds and publishes through PyPI trusted publishing, so there's no
token. The first release needs a pending publisher registered on PyPI for
`podpack-lablog`. `tools/publish.py` makes the same checks from a laptop.
