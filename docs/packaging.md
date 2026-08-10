# Packaging & distribution

## Distribution name: `equinox-api`

The PyPI *distribution* name is `equinox-api`, set in `pyproject.toml`'s
`[project] name`. The PyPI name `equinox` belongs to an unrelated project
([Patrick Kidger's JAX neural-network library](https://pypi.org/project/equinox/)),
so this project can never publish under that name.

The distribution name and the *import* package name are independent -
`import equinox` is unaffected by this. No source changes were needed for
this rename.

## The import package still collides — deliberately unresolved

This project's import package is also named `equinox`
(`src/equinox/`). If it were ever installed alongside Kidger's `equinox`
package in the same environment, one would silently shadow the other in
`site-packages` - whichever installed second wins, and the result is a
broken environment with no clear error.

This does not block development or the current `git clone` + `pip install
-e .` workflow, since nobody installs both packages by that name into the
same environment today. It **does** block a real public PyPI release:
once `equinox-api` is published, any user who also has (or later installs)
Kidger's `equinox` risks exactly that collision.

**Renaming the import package is a prerequisite for public release, not
optional polish.** It touches every `from equinox...` / `import equinox`
across roughly 60,000 lines of `src/` and 37,000 lines of `tests/`, so it
is deliberately not done now - it is significant, mechanical, easy-to-review
work best done in one dedicated pass immediately before an actual public
release, not speculatively ahead of one.

## What is intentionally not set up yet

- **No PyPI publishing.** `release.yml`'s `publish` job builds artifacts and
  creates a GitHub Release only. There is no `PYPI_API_TOKEN`, and none
  should be added until the import-name rename above is done - publishing
  under `equinox-api` while `import equinox` still collides would ship the
  problem to users instead of preventing it.
- **No installers or prebuilt binaries** (PyInstaller, briefcase, etc.).
  Install path remains `git clone` + `pip install -e ".[dev]"`.

Both are reasonable next steps once there's an actual push toward public
distribution - see the release-planning notes for the broader picture.
