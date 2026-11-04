# Linux System Work

This repo is only for work with configuration ans stuff on this linux ubuntu 26.04 VM. The host is a Windows 11. I am using vmware player in the latest version. 

## Usage

    uv run system collect

Collects basic system info (OS, hardware, CPU, memory, disks, network, failed services) and writes it to `reports/<hostname>/system-info.json`. Each machine gets its own directory, so the repo can be cloned and run on several systems; git history keeps older snapshots.

    .venv/bin/system cleanup            # dry run: shows what would be removed
    .venv/bin/system cleanup --apply    # actually do it

`make cleanup` is a shortcut for the `--apply` line. Run `cleanup` through `.venv/bin/system`, not `uv run system`: `uv run` holds the uv cache lock while it runs, so the `uv cache prune` step cannot get the lock. The script detects this and skips the uv step with a hint.

Frees disk space by running `uv cache prune` (drops cache entries nothing references any more), deleting rotated logs in `/var/log` older than `--days` (default 30), vacuuming the systemd journal to the same age, and removing old (disabled) snap revisions, which snap keeps for rollback. Log, journal and snap steps use `sudo`. Use `--skip-uv`, `--skip-snap` or `--skip-logs` to leave a part alone.

    make postgres        # or: uv run system postgres

Shows installed PostgreSQL server versions, the clusters from `pg_lsclusters` with their running version, and every database with its size. Flags unused installed versions, clusters older than the newest installed version, and servers that need a restart after a package update. It connects as your own user first and falls back to `sudo -u <cluster owner>`, so run it in a terminal. The result is also saved to `reports/<hostname>/postgres.json`.

    make pyversions      # or: uv run system pyversions [--root DIR]... [--target 3.14]
    make space           # or: uv run system space [--root DIR]...

`pyversions` scans project roots (default `/srv/python` and `~/python`, symlinks followed) and reports each project's `.python-version`, `requires-python` and the Python its `.venv` really runs, against the latest version uv knows for `--target`. It flags wrong pins, venvs that are behind or broken, and venvs a running process uses. `space` lists the biggest reclaimable items (uv cache and Pythons, project venvs, `~/.cache` entries, trash, disabled snap revisions, apt cache, journal, docker). Both are read-only and save to `reports/<hostname>/`.

    make projects        # classification, stale lockfiles, backup risks
    make audit           # same plus uv audit (needs network)
    .venv/bin/system projects --archive ~/python/foo   # tar.gz just this project (originals kept)
    .venv/bin/system projects --archive   # tar.gz the projects marked "archive" (originals kept)
    .venv/bin/system projects --apply     # remove the .venv of "idea"/"archive" projects

`projects` reads `projects.toml` (one section per host, keyed by project path) with the statuses `active`, `grey`, `idea` and `archive`. Audits follow the classification, not the `.venv`: `active` and `grey` projects are always audited, unclassified ones when they have a `.venv`. Archives go to the host's `archive_dir` (default `~/archive`, keep it outside Nextcloud) as `.tar.gz` with a `.sha256`, including `.git` but not venvs. Screening only reports; fixing audit findings (upgrading dependencies) is done per project in a separate session.

    make repos           # or: .venv/bin/system repos [--root DIR]... [--files] [--open] [--sort path|commit|changed] [--push|--pull [--apply]]

`repos` lists the git repositories directly under the project roots (any language) with their status: uncommitted changes (staged, modified, untracked, conflicts), unpushed commits, no remote or upstream, or clean (shown in green). Each line also names the dependency tools the repo uses (from lock/manifest files and `pyproject.toml`: uv, poetry, pdm, pipenv, pip, hatch, setuptools, npm, composer). Output is colored for light terminal themes when it goes to a terminal; set `NO_COLOR` to turn that off. Two dates per repo: `commit` is the last commit, `changed` the newest file in the working tree (tracked or untracked; ignored files and `.python-version` do not count, and a fresh clone looks recently changed because checkout sets file times). `--sort commit` or `--sort changed` lists the oldest first. `--files` shows the changed paths, `--open` hides the clean repos. It only runs `git status` (no fetch), so "behind" is as of the last fetch. Saved to `reports/<hostname>/repos.json`.

`repos --push` lists the repos whose current branch has unpushed commits; `--push --apply` runs `git push` in each. Only committed work is pushed (uncommitted changes stay local and are flagged). Repos without an upstream, or that are also behind their upstream (diverged, push would be rejected), are skipped with the reason. Exits non-zero if any push fails.

`repos --pull` first runs `git fetch` in every repo with an upstream (no prompts, 60 s timeout each), then lists the repos that are behind. Only repos with nothing open (no staged, modified, untracked or conflicting files and no unpushed commits) are pulled, and only with `git pull --ff-only` under `--apply`. Dirty or diverged repos are reported with the reason and left alone; nothing is merged, rebased, stashed or forced. The dry run updates remote-tracking refs but not working trees.

## Claude Code skill

`.claude/skills/systemcheck/` is a Claude Code skill: ask for a "system check" and it runs `collect`, `pyversions`, `space` (and `postgres` where installed), diffs the reports against the last commit and summarizes what needs attention. It is read-only; it never cleans up or deletes anything on its own.
