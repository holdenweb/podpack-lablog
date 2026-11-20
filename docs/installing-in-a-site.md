# Installing podpack-lablog in a site: the commands

The README's *Installing it into a site* says what the four steps are. This
gives the commands, in order, for a site built on podpack's container
substrate: compose, `scripts/up.sh`, and a `secrets.env` holding the database
URI. Every one was run on 2026-11-19, installing 0.1.0 on
[podpack-demo](https://github.com/holdenweb/podpack-demo/blob/main/docs/installing-podpack-lablog.md)
and then on
[holdenweb.com](https://github.com/holdenweb/holdenweb-com/blob/main/ops/records/2026-11-19-lablog-install.md).
Those two logs show what each command printed.

Run everything on your own machine, in the site's checkout, unless a step
names a host. Throughout, `<site>` is the site's package name (`podpack_demo`,
`holdenweb`) and `<email>` is the account that is to hold the role.

The site needs podpack 0.9.0 or later. A site run without containers, on a
PostgreSQL of your own, differs at step 4, and isn't covered here; podpack's
`creating-a-site.md` step 8 is the recipe for it.

## 1. Baseline

Bring the site up as it is, so that anything failing later can be pinned on
lablog:

```bash
./scripts/up.sh
```

```bash
podman compose exec -T web alembic current
```

It should report `(head)`.

## 2. Add the distribution

```bash
uv add podpack-lablog
```

`git diff uv.lock` should only add lines, apart from anything lablog's own
minimums force: on holdenweb.com it moved WTForms from 3.3.1 to 3.3.2,
because lablog declares `wtforms>=3.3.2`.

## 3. Enable it

An edit to `config/app.toml`, not a command:

```toml
[site]
apps = [..., "podpack_lablog"]      # the import name

[apps.lablog]                       # the app's name
timezone = "Europe/London"          # optional; default "UTC"
```

Times are stored as UTC and only converted for display. The zone is resolved
from the system's zone files: podpack's image has them, because Debian's slim
image ships `tzdata`. On an image without them, any zone but `UTC` stops the
site booting.

## 4. Generate the revision

Apps ship no migrations, so the site generates lablog's tables in its own
alembic history. Generate on the host, against the engine the site deploys on,
with the full app list enabled: with an app missing, alembic proposes dropping
its tables.

The database publishes no port until asked:

```bash
podman compose --profile postgres-port up -d postgres-port
```

The URI in `secrets.env` names `postgres:5432`, which only works inside the
compose network. This subshell rewrites it to the published port, taking the
number from `.env`, without ever displaying the URI:

```bash
(
  set -a; . ./.env; . ./secrets.env; set +a
  export SQLALCHEMY_DATABASE_URI="${SQLALCHEMY_DATABASE_URI/@postgres:5432\//@127.0.0.1:${POSTGRES_HOST_PORT}/}"
  export PODPACK_CONFIG=config/app.toml
  uv run alembic revision --autogenerate -m "lablog: app schema"
)
```

It should detect exactly three tables: `lablog_project`, `lablog_experiment`
and `lablog_entry`. Alembic's notes about existing sequences ("assuming SERIAL
and omitting") are not proposed changes. Confirm that the revision creates
those three tables and drops nothing outside `downgrade()`:

```bash
grep -n -E 'def (up|down)grade|op\.' alembic/versions/*lablog*.py
```

On holdenweb.com that printed:

```
31:def upgrade() -> None:
34:    op.create_table('lablog_project',
46:    op.create_table('lablog_experiment',
64:    op.create_table('lablog_entry',
80:def downgrade() -> None:
83:    op.drop_table('lablog_entry')
84:    op.drop_table('lablog_experiment')
85:    op.drop_table('lablog_project')
```

Then close the port:

```bash
podman compose --profile postgres-port rm -sf postgres-port
```

## 5. Rebuild

`uv add` changed the lock, not the image, so rebuild. The `migrate` service
applies the revision before `web` starts:

```bash
./scripts/up.sh
```

```bash
podman compose exec -T web alembic current
```

It should now report the new revision as head.

## 6. Create the role and grant it

```bash
podman compose exec -T web flask --app <site> roles create lablog
```

```bash
podman compose exec -T web flask --app <site> roles add <email> lablog
```

If `roles create` is run a second time, it prints "created successfully" and
then fails with a `UniqueViolation`. The role exists either way.

## 7. Check it

Signed out, a visitor is sent to log in. `<port>` is `WEB_HOST_PORT` from
`.env`:

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://localhost:<port>/lablog/
```

Expect `302 …/login?next=/lablog/`. Signed in with the role, `/lablog/` loads.
As an administrator, `/_status` shows
`"reported": {"holders": 1, "role": "lablog", "role_exists": true}`.

A site's own tests can keep this checked. holdenweb.com's
`tests/test_lablog.py` signs in users it creates itself and asserts the
stranger's 404. Its browser suite carries no credential, so it checks only the
gate from outside, in `test_the_lablog_app_is_installed_and_closed_to_visitors`.

## 8. Commit

`pyproject.toml`, `uv.lock`, `config/app.toml` and the new revision.

## On each deployed host

Deploy as the site normally does; on holdenweb.com that's
`./ops/deploy.sh staging`, then `production`. The `migrate` service applies the
revision there as it did locally. Then run step 6 in that host's checkout:
each deployment has its own database, so each needs its own role. Until the
role exists and someone holds it, every lablog page on that host answers 404
to everyone, the administrator included.
