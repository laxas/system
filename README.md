# Linux System Work

This repo is only for work with configuration ans stuff on this linux ubuntu 26.04 VM. The host is a Windows 11. I am using vmware player in the latest version. 

## Usage

    uv run system collect

Collects basic system info (OS, hardware, CPU, memory, disks, network, failed services) and writes it to `reports/<hostname>/system-info.json`. Each machine gets its own directory, so the repo can be cloned and run on several systems; git history keeps older snapshots.

    .venv/bin/system cleanup            # dry run: shows what would be removed
    .venv/bin/system cleanup --apply    # actually do it

`make cleanup` is a shortcut for the `--apply` line. Run `cleanup` through `.venv/bin/system`, not `uv run system`: `uv run` holds the uv cache lock while it runs, so the `uv cache prune` step cannot get the lock. The script detects this and skips the uv step with a hint.

Frees disk space by running `uv cache prune` (drops cache entries nothing references any more), deleting rotated logs in `/var/log` older than `--days` (default 30), and vacuuming the systemd journal to the same age. Log and journal deletion use `sudo`. Use `--skip-uv` or `--skip-logs` to leave a part alone.

    make postgres        # or: uv run system postgres

Shows installed PostgreSQL server versions, the clusters from `pg_lsclusters` with their running version, and every database with its size. Flags unused installed versions, clusters older than the newest installed version, and servers that need a restart after a package update. It connects as your own user first and falls back to `sudo -u <cluster owner>`, so run it in a terminal. The result is also saved to `reports/<hostname>/postgres.json`.

    make pyversions      # or: uv run system pyversions [--root DIR]... [--target 3.14]
    make space           # or: uv run system space [--root DIR]...

`pyversions` scans project roots (default `/srv/python` and `~/python`, symlinks followed) and reports each project's `.python-version`, `requires-python` and the Python its `.venv` really runs, against the latest version uv knows for `--target`. It flags wrong pins, venvs that are behind or broken, and venvs a running process uses. `space` lists the biggest reclaimable items (uv cache and Pythons, project venvs, `~/.cache` entries, trash, disabled snap revisions, apt cache, journal, docker). Both are read-only and save to `reports/<hostname>/`.

## Claude Code skill

`.claude/skills/systemcheck/` is a Claude Code skill: ask for a "system check" and it runs `collect`, `pyversions`, `space` (and `postgres` where installed), diffs the reports against the last commit and summarizes what needs attention. It is read-only; it never cleans up or deletes anything on its own.
