"""Free disk space: prune the uv cache, delete old rotated logs, vacuum the journal.

Everything is a dry run unless apply=True.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from system.util import human

LOG_DIR = Path("/var/log")
# Rotated/compressed leftovers: syslog.1, syslog.2.gz, foo.log.old, ...
ROTATED = re.compile(r"\.(gz|xz|bz2|zst|old|\d+)$")
# Never touched here; journald manages it (see vacuum_journal).
SKIP_DIRS = {"journal"}


def privileged(*cmd: str) -> list[str]:
    return list(cmd) if os.geteuid() == 0 else ["sudo", *cmd]


def uv_cache_size() -> int | None:
    result = subprocess.run(
        ["uv", "cache", "size", "--preview-features", "cache-size"], capture_output=True, text=True
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def clean_uv_cache(apply: bool) -> None:
    print("== uv cache")
    if shutil.which("uv") is None:
        print("uv not found, skipping")
        return
    before = uv_cache_size()
    print(f"current size: {human(before) if before is not None else 'unknown'}")
    if "UV_RUN_RECURSION_DEPTH" in os.environ:
        # `uv run` holds the cache lock while our process runs, so prune would just wait for us.
        print(
            "cannot prune from inside `uv run`: it holds the uv cache lock.\n"
            f"run it directly instead: {sys.prefix}/bin/system cleanup --apply"
        )
        return
    if not apply:
        print("would run: uv cache prune (uv has no dry run; it removes unreferenced entries)")
        return
    # Fail fast if another uv process really is using the cache, instead of waiting 5 minutes.
    result = subprocess.run(["uv", "cache", "prune"], env={**os.environ, "UV_LOCK_TIMEOUT": "30"})
    if result.returncode != 0:
        print(f"uv cache prune failed (exit {result.returncode}), continuing")
        return
    after = uv_cache_size()
    if before is not None and after is not None:
        print(f"freed: {human(before - after)} (now {human(after)})")


def old_rotated_logs(days: int) -> list[tuple[Path, int]]:
    cutoff = time.time() - days * 86400
    found = []
    for root, dirs, files in os.walk(LOG_DIR, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(root) == LOG_DIR and d in SKIP_DIRS)]
        for name in files:
            if not ROTATED.search(name):
                continue
            path = Path(root, name)
            try:
                st = path.lstat()
            except OSError:
                continue
            # Regular files only: never follow or delete symlinks.
            if path.is_symlink() or not path.is_file() or st.st_mtime >= cutoff:
                continue
            found.append((path, st.st_size))
    return found


def clean_logs(days: int, apply: bool) -> None:
    print(f"== rotated logs in {LOG_DIR} older than {days} days")
    files = old_rotated_logs(days)
    total = sum(size for _, size in files)
    for path, size in sorted(files):
        print(f"  {human(size):>10}  {path}")
    print(f"{len(files)} files, {human(total)}")
    if apply and files:
        subprocess.run(privileged("rm", "-f", "--", *map(str, (p for p, _ in files))), check=True)
        print("deleted")


def vacuum_journal(days: int, apply: bool) -> None:
    print(f"== systemd journal older than {days} days")
    if shutil.which("journalctl") is None:
        print("journalctl not found, skipping")
        return
    subprocess.run(["journalctl", "--disk-usage"], check=False)
    if not apply:
        print(f"would run: journalctl --vacuum-time={days}d")
        return
    subprocess.run(privileged("journalctl", f"--vacuum-time={days}d"), check=True)


def cleanup(days: int = 30, apply: bool = False, uv: bool = True, logs: bool = True) -> None:
    sys.stdout.reconfigure(line_buffering=True)  # keep our output ordered with subprocess output
    if not apply:
        print("DRY RUN - nothing is deleted. Re-run with --apply to clean up.\n")
    if uv:
        clean_uv_cache(apply)
        print()
    if logs:
        clean_logs(days, apply)
        print()
        vacuum_journal(days, apply)
    if apply:
        usage = shutil.disk_usage("/")
        print(f"\n/ now has {human(usage.free)} free")
