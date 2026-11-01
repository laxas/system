"""Find where disk space goes and which of it is easy to reclaim. Read-only."""

import json
import os
import re
import shutil
import socket
import subprocess
from datetime import datetime
from pathlib import Path

from system.cleanup import disabled_snaps
from system.pyversions import C_ENV, DEFAULT_ROOTS, find_projects
from system.util import human

MIN_CACHE_ENTRY = 100 * 2**20  # only list ~/.cache entries above 100 MiB


def dir_size(path: Path) -> int | None:
    """Bytes used under path, via du (handles hardlinks and unreadable files better than a Python walk)."""
    if not path.exists():
        return None
    try:
        result = subprocess.run(["du", "-sb", "--", str(path)], capture_output=True, text=True, timeout=600, env=C_ENV)
    except subprocess.TimeoutExpired:
        return None
    try:
        return int(result.stdout.split()[0])  # partial results are fine if some files were unreadable
    except (IndexError, ValueError):
        return None


def item(category: str, label: str, size: int | None, hint: str) -> dict:
    return {"category": category, "label": label, "size_bytes": size, "hint": hint}


def uv_items() -> list[dict]:
    items = []
    if shutil.which("uv"):
        cache = Path(subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True).stdout.strip())
        items.append(item("uv", f"uv cache {cache}", dir_size(cache), "uv cache clean (re-downloads packages on next sync)"))
    python_dir = Path("~/.local/share/uv/python").expanduser()
    for child in sorted(python_dir.glob("cpython-*")) if python_dir.is_dir() else []:
        if not child.is_symlink():
            items.append(item("uv python", f"uv python {child.name}", dir_size(child), "uv python uninstall <version>, if no venv uses it"))
    return items


def venv_items(roots: list[str]) -> list[dict]:
    return [
        item("venv", f"venv {p}/.venv", dir_size(p / ".venv"), "rebuildable with uv sync")
        for p in find_projects(roots)
        if (p / ".venv").exists()
    ]


def cache_items() -> list[dict]:
    cache = Path("~/.cache").expanduser()
    items = []
    for child in sorted(cache.iterdir()) if cache.is_dir() else []:
        if child.name == "uv":
            continue  # reported separately
        size = dir_size(child)
        if size and size >= MIN_CACHE_ENTRY:
            items.append(item("cache", f"~/.cache/{child.name}", size, "downloaded data, usually safe to delete"))
    trash = Path("~/.local/share/Trash").expanduser()
    items.append(item("trash", "trash", dir_size(trash), "empty it"))
    return items


def snap_items() -> list[dict]:
    snaps = disabled_snaps()
    if not snaps:
        return []
    return [item("snap", f"{len(snaps)} disabled snap revisions", sum(size for _, _, size in snaps), "system cleanup (removes them via sudo)")]


def system_items() -> list[dict]:
    items = []
    apt = Path("/var/cache/apt/archives")
    items.append(item("apt", "apt package cache", dir_size(apt), "sudo apt clean"))
    journal = subprocess.run(["journalctl", "--disk-usage"], capture_output=True, text=True, env=C_ENV).stdout
    match = re.search(r"([\d.]+)([KMGT])", journal)
    if match:
        size = float(match[1]) * 1024 ** "KMGT".index(match[2]) * 1024
        items.append(item("journal", "systemd journal", int(size), "journalctl --vacuum-time=30d"))
    if shutil.which("docker"):
        result = subprocess.run(
            ["docker", "system", "df", "--format", "{{json .}}"], capture_output=True, text=True, env=C_ENV
        )
        for line in result.stdout.splitlines():
            row = json.loads(line)
            if row["Reclaimable"] not in ("0B", "0"):
                items.append(item("docker", f"docker {row['Type'].lower()}: {row['Reclaimable']} reclaimable", None, "docker system prune"))
    return items


def survey(roots: list[str]) -> dict:
    usage = shutil.disk_usage("/")
    items = uv_items() + venv_items(roots) + cache_items() + snap_items() + system_items()
    items.sort(key=lambda i: i["size_bytes"] or 0, reverse=True)
    return {
        "hostname": socket.gethostname(),
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "root_total_bytes": usage.total,
        "root_free_bytes": usage.free,
        "items": items,
    }


def show(info: dict) -> None:
    total, free = info["root_total_bytes"], info["root_free_bytes"]
    print(f"/ has {human(free)} free of {human(total)} ({100 - free / total * 100:.0f}% used)\n")
    for it in info["items"]:
        size = human(it["size_bytes"]) if it["size_bytes"] is not None else "-"
        print(f"{size:>10}  {it['label']}")
        print(f"{'':>10}    -> {it['hint']}")
