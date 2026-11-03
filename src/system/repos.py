"""Git repositories with uncommitted changes or unpushed commits.

Scans the immediate subdirectories of each root that are git repositories (any language, not only
Python projects). Read-only: runs `git status` only, no fetch, so "behind" reflects the last fetch.
"""

import socket
import subprocess
from datetime import datetime
from pathlib import Path


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


def summary(r: dict) -> str:
    parts = [
        f"{len(r[k])} {label}"
        for k, label in (
            ("conflicts", "conflicts"),
            ("staged", "staged"),
            ("modified", "modified"),
            ("untracked", "untracked"),
        )
        if r[k]
    ]
    if r["ahead"]:
        parts.append(f"{r['ahead']} unpushed")
    if r["behind"]:
        parts.append(f"{r['behind']} behind")
    if not r["has_remote"]:
        parts.append("no remote")
    elif r["upstream"] is None:
        parts.append("no upstream")
    return ", ".join(parts)


def show(info: dict, files: bool = False, show_all: bool = False) -> None:
    repos = info["repos"]
    listed = [r for r in repos if show_all or needs_attention(r)]
    width = max((len(r["repo"]) for r in listed), default=10)
    for r in listed:
        if "error" in r:
            print(f"{r['repo']:<{width}}  ERROR: {r['error']}")
            continue
        print(f"{r['repo']:<{width}}  [{r['branch']}] {summary(r) or 'clean'}")
        if files:
            for k, mark in (
                ("conflicts", "U"),
                ("staged", "S"),
                ("modified", "M"),
                ("untracked", "?"),
            ):
                for path in r[k]:
                    print(f"{'':<{width}}    {mark} {path}")
    dirty = sum(1 for r in repos if r.get("dirty"))
    unpushed = sum(1 for r in repos if r.get("ahead"))
    print(
        f"\n{len(repos)} repos: {dirty} with uncommitted changes, {unpushed} with unpushed commits"
    )
