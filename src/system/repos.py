"""Git repositories with uncommitted changes or unpushed commits.

Scans the immediate subdirectories of each root that are git repositories (any language, not only
Python projects). Read-only: runs `git status` only, no fetch, so "behind" reflects the last fetch.
"""

import os
import socket
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

# Marker file -> dependency/packaging tool, in display order.
TOOL_MARKERS = (
    ("uv.lock", "uv"),
    ("poetry.lock", "poetry"),
    ("pdm.lock", "pdm"),
    ("Pipfile", "pipenv"),
    ("requirements.txt", "pip"),
    ("setup.py", "setuptools"),
    ("package.json", "npm"),
    ("composer.json", "composer"),
)

# ANSI colors chosen for a light terminal background (no yellow/cyan, which wash out on white).
COLORS = {
    "conflicts": "1;31",
    "staged": "32",
    "modified": "31",
    "untracked": "35",
    "unpushed": "34",
    "behind": "1;34",
    "no remote": "1;31",
    "no upstream": "35",
    "clean": "32",
    "error": "1;31",
    "branch": "90",
    "tool": "90",
    "repo": "1",
}


def find_repos(roots: list[str]) -> list[Path]:
    seen: set[Path] = set()
    repos = []
    for root in roots:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            real = child.resolve()
            if real not in seen and child.is_dir() and (child / ".git").exists():
                seen.add(real)
                repos.append(child)
    return repos


def parse_status(output: str) -> dict:
    """Parse `git status --porcelain=v2 --branch` (one entry per line)."""
    info: dict = {
        "branch": None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
        "staged": [],
        "modified": [],
        "untracked": [],
        "conflicts": [],
    }
    for line in output.splitlines():
        if line.startswith("# branch.head "):
            info["branch"] = line.removeprefix("# branch.head ")
        elif line.startswith("# branch.upstream "):
            info["upstream"] = line.removeprefix("# branch.upstream ")
        elif line.startswith("# branch.ab "):
            ahead, behind = line.removeprefix("# branch.ab ").split()
            info["ahead"], info["behind"] = int(ahead), -int(behind)
        elif line.startswith(("1 ", "2 ")):
            fields = line.split(" ", 8 if line[0] == "1" else 9)
            xy, path = fields[1], fields[-1].split("\t")[0]
            if xy[0] != ".":
                info["staged"].append(path)
            if xy[1] != ".":
                info["modified"].append(path)
        elif line.startswith("u "):
            info["conflicts"].append(line.split(" ", 10)[-1])
        elif line.startswith("? "):
            info["untracked"].append(line[2:])
    return info


def detect_tools(repo: Path) -> list[str]:
    """Dependency/packaging tools a repo uses, from its lock and manifest files."""
    tools = [tool for marker, tool in TOOL_MARKERS if (repo / marker).exists()]
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text())
    except OSError, tomllib.TOMLDecodeError:
        data = None
    if data is not None:
        tool_cfg = data.get("tool", {})
        backend = data.get("build-system", {}).get("build-backend", "")
        for name, used in (
            ("uv", "uv" in tool_cfg or backend.startswith("uv_build")),
            ("poetry", "poetry" in tool_cfg or backend.startswith("poetry")),
            ("pdm", "pdm" in tool_cfg or backend.startswith("pdm")),
            ("hatch", "hatch" in tool_cfg or backend.startswith("hatchling")),
        ):
            if used and name not in tools:
                tools.append(name)
        if not tools:
            tools.append("pyproject")
    return tools


def repo_status(repo: Path) -> dict:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v2", "--branch"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {
            "repo": str(repo),
            "error": result.stderr.strip() or "git status failed",
        }
    info = parse_status(result.stdout)
    info["repo"] = str(repo)
    info["tools"] = detect_tools(repo)
    info["has_remote"] = bool(
        subprocess.run(
            ["git", "-C", str(repo), "remote"], capture_output=True, text=True
        ).stdout.strip()
    )
    info["dirty"] = any(
        info[k] for k in ("staged", "modified", "untracked", "conflicts")
    )
    return info


def survey(roots: list[str]) -> dict:
    return {
        "hostname": socket.gethostname(),
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "roots": roots,
        "repos": [repo_status(r) for r in find_repos(roots)],
    }


def needs_attention(r: dict) -> bool:
    return "error" in r or r["dirty"] or r["ahead"] > 0 or r["upstream"] is None


def use_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def paint(text: str, key: str, color: bool) -> str:
    return f"\033[{COLORS[key]}m{text}\033[0m" if color else text


def summary(r: dict, color: bool = False) -> str:
    parts = [
        paint(f"{len(r[k])} {k}", k, color)
        for k in ("conflicts", "staged", "modified", "untracked")
        if r[k]
    ]
    if r["ahead"]:
        parts.append(paint(f"{r['ahead']} unpushed", "unpushed", color))
    if r["behind"]:
        parts.append(paint(f"{r['behind']} behind", "behind", color))
    if not r["has_remote"]:
        parts.append(paint("no remote", "no remote", color))
    elif r["upstream"] is None:
        parts.append(paint("no upstream", "no upstream", color))
    return ", ".join(parts) or paint("clean", "clean", color)


def show(info: dict, files: bool = False, show_all: bool = False) -> None:
    color = use_color()
    repos = info["repos"]
    listed = [r for r in repos if show_all or needs_attention(r)]
    width = max((len(r["repo"]) for r in listed), default=10)
    tools_of = {r["repo"]: "+".join(r.get("tools") or ["-"]) for r in listed}
    tool_width = max((len(t) for t in tools_of.values()), default=1)
    branch_width = max((len(r.get("branch") or "") for r in listed), default=1) + 2
    for r in listed:
        name = paint(f"{r['repo']:<{width}}", "repo", color)
        if "error" in r:
            print(f"{name}  {paint('ERROR: ' + r['error'], 'error', color)}")
            continue
        tools = paint(f"{tools_of[r['repo']]:<{tool_width}}", "tool", color)
        branch = paint(
            f"{'[' + str(r['branch']) + ']':<{branch_width}}", "branch", color
        )
        print(f"{name}  {tools}  {branch} {summary(r, color)}")
        if files:
            for k, mark in (
                ("conflicts", "U"),
                ("staged", "S"),
                ("modified", "M"),
                ("untracked", "?"),
            ):
                for path in r[k]:
                    print(f"{'':<{width}}    {paint(mark, k, color)} {path}")
    dirty = sum(1 for r in repos if r.get("dirty"))
    unpushed = sum(1 for r in repos if r.get("ahead"))
    print(
        f"\n{len(repos)} repos: {dirty} with uncommitted changes, {unpushed} with unpushed commits"
    )
