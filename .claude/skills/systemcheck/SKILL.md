---
name: systemcheck
description: Run the repo's read-only system diagnostics (system info, Python versions/venvs, disk space, PostgreSQL), compare with the last committed report for this host, and summarize what needs attention. Use when the user asks for a system check, health check, "what changed on this machine", or disk/venv/Python-version status.
---

# systemcheck

This repo (`/srv/python/system`, or wherever it is cloned) holds diagnostics for Ubuntu VMs.
Each command saves JSON to `reports/<hostname>/`; reports are committed, so git history is the
baseline for "what changed".

## Steps

1. Work from the repo root. Run the read-only checks with the venv binary, one by one:
   - `.venv/bin/system collect`
   - `.venv/bin/system pyversions`
   - `.venv/bin/system space` (can take a minute: it measures directory sizes)
   - `.venv/bin/system projects --audit` (classification from `projects.toml`, stale lockfiles and
     `uv audit` findings; needs network, about 30 s)
   - Run `.venv/bin/system postgres` only if `pg_lsclusters` exists. It may need the user's sudo
     password and cannot prompt inside this session: if it reports "terminal is required", tell the
     user to run `! make postgres` themselves, then read the result.
   If `.venv` is missing, run `uv sync` first.
2. Compare with the last commit: `git diff -- reports/$(hostname)/`. Ignore noise (`collected_at`,
   `uptime`, `load_avg`, `available_mib`); report real changes (failed units, new/removed databases,
   interpreter versions, free disk space).
3. Read the new reports in `reports/$(hostname)/` and give a short, prioritized summary:
   - **Urgent**: root disk above 85% used, failed systemd units or `degraded` state, a service
     running from a venv that is broken or outdated.
   - **Dependency audit**: for `active` and `grey` projects first, then unclassified ones: number of
     vulnerabilities and the worst packages with their fix versions, plus lockfiles older than the limit.
     Also point out unclassified projects (they need a status in `projects.toml`) and code with no
     git, no commits or no remote.
   - **Attention**: the `Needs attention` items from `pyversions`, PostgreSQL findings (unused
     installed version, cluster older than the newest installed one, restart needed).
   - **Reclaimable space**: the top few items from `space` with their hint, and what they would free.
   - **Changed since last report**: from the diff.
   Keep it to what matters; do not paste whole reports.
4. Offer next steps, then stop. Ask before committing the refreshed reports.

## Rules

- Screening only reports. Never fix audit findings during a check: no `uv lock --upgrade`,
  `uv add`, `uv sync`, no editing of lockfiles or `pyproject.toml`. Upgrading dependencies needs
  tests and judgement and is done per project in a separate coding session. Offer that as the next
  step, listing the projects and the packages to upgrade from `reports/<host>/projects.json`.
- This skill is read-only. Never run `cleanup --apply`, `uv cache clean`, `uv python uninstall`,
  `snap remove`, `apt` changes or delete any `.venv` unless the user asks for that specific action.
  `.venv/bin/system cleanup` without `--apply` is a safe dry run.
- Run `cleanup` as `.venv/bin/system cleanup` (or `make cleanup`), never `uv run system cleanup`:
  `uv run` holds the uv cache lock, so `uv cache prune` would block.
- Other `.python-version` conflicts are the user's call: some projects deliberately pin an older
  Python through `requires-python`; report them, do not change them.
