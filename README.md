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
