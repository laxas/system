"""Audit Python versions: .python-version files, venv interpreters, uv-managed Pythons.

Read-only. Project roots are followed through symlinks (e.g. ~/python -> ~/Nextcloud/python).
"""

import json
import os
import re
import socket
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path

DEFAULT_ROOTS = ["/srv/python", "~/python"]
DEFAULT_TARGET = "3.14"
C_ENV = {**os.environ, "LC_ALL": "C", "LANG": "C"}


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def uv_pythons(target: str) -> list[dict]:
    """cpython (non-freethreaded) entries uv knows for this minor version, installed or downloadable."""
    result = subprocess.run(
        ["uv", "python", "list", target, "--output-format", "json"], capture_output=True, text=True, env=C_ENV
    )
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [e for e in entries if e["implementation"] == "cpython" and e["variant"] == "default"]


def find_projects(roots: list[str]) -> list[Path]:
    """Immediate subdirectories of each root that look like Python projects."""
    seen: set[Path] = set()
    projects = []
    for root in roots:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            real = child.resolve()
            if real in seen or not child.is_dir():
                continue
            if (child / ".python-version").exists() or (child / "pyproject.toml").exists():
                seen.add(real)
                projects.append(child)
    return projects


def read_requires_python(project: Path) -> str | None:
    try:
        data = tomllib.loads((project / "pyproject.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data.get("project", {}).get("requires-python") or data.get("tool", {}).get("poetry", {}).get(
        "dependencies", {}
    ).get("python")


def allows(spec: str, version: str) -> bool:
    """Check >=, >, <=, < clauses only; any other clause is treated as allowing the version."""
    for op, bound in re.findall(r"(>=|<=|>|<)\s*([\d.]+)", spec):
        cmp = (version_key(version) > version_key(bound)) - (version_key(version) < version_key(bound))
        if (op == ">=" and cmp < 0) or (op == ">" and cmp <= 0) or (op == "<=" and cmp > 0) or (op == "<" and cmp >= 0):
            return False
    return True


def venv_python(project: Path) -> dict:
    python = project / ".venv" / "bin" / "python"
    if not (project / ".venv").exists():
        return {"status": "none", "version": None}
    try:
        result = subprocess.run(
            [str(python), "-c", "import platform; print(platform.python_version())"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "broken", "version": None}
    if result.returncode != 0:
        return {"status": "broken", "version": None}
    return {"status": "ok", "version": result.stdout.strip()}


def processes_in(venvs: dict[Path, Path]) -> dict[Path, list[int]]:
    """Map project -> pids whose command line runs from that project's venv."""
    found: dict[Path, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue  # skip ourselves: we run from this repo's venv
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        for project, venv in venvs.items():
            if str(venv) + "/bin" in cmdline:
                found.setdefault(project, []).append(int(entry.name))
    return found


def audit(roots: list[str], target: str) -> dict:
    pythons = uv_pythons(target)
    latest = max((p["version"] for p in pythons), key=version_key, default=None)
    projects = find_projects(roots)
    running = processes_in({p: (p / ".venv").resolve() for p in projects})

    rows = []
    for project in projects:
        file = project / ".python-version"
        pinned = file.read_text().strip() if file.exists() else None
        requires = read_requires_python(project)
        venv = venv_python(project)
        notes = []
        if pinned is None:
            notes.append("no .python-version")
        elif pinned != target:
            notes.append(f".python-version is {pinned}, expected {target}")
        if requires and latest and not allows(requires, latest):
            notes.append(f"requires-python {requires} excludes {latest}")
        if venv["status"] == "broken":
            notes.append("venv has no working interpreter: rebuild")
        elif venv["version"] and latest and version_key(venv["version"]) < version_key(latest):
            notes.append(f"venv runs {venv['version']}, latest is {latest}")
        if project in running:
            notes.append(f"running from this venv (pids {running[project]}): restart after changing it")
        rows.append(
            {
                "project": str(project),
                "python_version_file": pinned,
                "requires_python": requires,
                "venv": venv,
                "running_pids": running.get(project, []),
                "notes": notes,
            }
        )

    used = {r["venv"]["version"] for r in rows if r["venv"]["version"]}
    return {
        "hostname": socket.gethostname(),
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target": target,
        "latest_available": latest,
        "interpreters": sorted({p["version"] for p in pythons if p["path"]}, key=version_key),
        "venv_versions_in_use": sorted(used, key=version_key),
        "projects": rows,
    }


def show(info: dict) -> None:
    print(f"target {info['target']}  latest available: {info['latest_available'] or 'unknown'}")
    print(f"interpreters uv found (managed and system): {', '.join(info['interpreters']) or 'none'}")
    print(f"venv interpreters in use (scanned projects only): {', '.join(info['venv_versions_in_use']) or 'none'}\n")
    width = max((len(r["project"]) for r in info["projects"]), default=10)
    print(f"{'project':<{width}}  {'file':<8} {'venv':<9} requires-python")
    for r in info["projects"]:
        venv = r["venv"]["version"] or r["venv"]["status"]
        print(f"{r['project']:<{width}}  {r['python_version_file'] or '-':<8} {venv:<9} {r['requires_python'] or '-'}")
    print("\nNeeds attention:")
    flagged = [r for r in info["projects"] if r["notes"]]
    for r in flagged:
        print(f"  {r['project']}")
        for note in r["notes"]:
            print(f"    - {note}")
    if not flagged:
        print("  nothing")
