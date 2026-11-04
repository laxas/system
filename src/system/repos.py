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
    "date": "34;2",
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


def git_out(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else ""


def iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def last_commit(repo: Path) -> str | None:
    out = git_out(repo, "log", "-1", "--format=%ct").strip()
    return iso(float(out)) if out else None


# Tooling metadata that bulk edits touch (e.g. re-pinning Python); it says nothing about work on the code.
NOT_A_CHANGE = {".python-version"}


def last_changed_file(repo: Path) -> tuple[str | None, str | None]:
    """(time, path) of the newest file in the working tree, tracked or untracked.

    Ignored files (.venv, caches, build output) and NOT_A_CHANGE do not count. Note that checkout/clone sets
    mtimes, so a fresh clone looks recently changed.
    """
    newest, newest_path = None, None
    listing = git_out(
        repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
    )
    for rel in filter(None, listing.split("\0")):
        if Path(rel).name in NOT_A_CHANGE:
            continue
        try:
            mtime = (repo / rel).lstat().st_mtime
        except OSError:  # tracked but deleted
            continue
        if newest is None or mtime > newest:
            newest, newest_path = mtime, rel
    return iso(newest), newest_path


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
    info["last_commit"] = last_commit(repo)
    info["last_changed"], info["last_changed_file"] = last_changed_file(repo)
    info["has_remote"] = bool(git_out(repo, "remote").strip())
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


SORT_KEYS = ("path", "commit", "changed")


def day(value: str | None) -> str:
    return value[:10] if value else "-"


def show(
    info: dict, files: bool = False, show_all: bool = False, sort: str = "path"
) -> None:
    color = use_color()
    repos = info["repos"]
    listed = [r for r in repos if show_all or needs_attention(r)]
    if sort != "path":  # oldest first, so stale repos come to the top
        field = "last_commit" if sort == "commit" else "last_changed"
        listed.sort(key=lambda r: r.get(field) or "")
    width = max((len(r["repo"]) for r in listed), default=10)
    tools_of = {r["repo"]: "+".join(r.get("tools") or ["-"]) for r in listed}
    tool_width = max((len(t) for t in tools_of.values()), default=1)
    branch_width = max((len(r.get("branch") or "") for r in listed), default=1) + 2
    print(
        paint(
            f"{'repo':<{width}}  {'tools':<{tool_width}}  {'branch':<{branch_width}}"
            f" {'commit':<10}  {'changed':<10}  status",
            "branch",
            color,
        )
    )
    for r in listed:
        name = paint(f"{r['repo']:<{width}}", "repo", color)
        if "error" in r:
            print(f"{name}  {paint('ERROR: ' + r['error'], 'error', color)}")
            continue
        tools = paint(f"{tools_of[r['repo']]:<{tool_width}}", "tool", color)
        branch = paint(
            f"{'[' + str(r['branch']) + ']':<{branch_width}}", "branch", color
        )
        dates = paint(
            f"{day(r['last_commit']):<10}  {day(r['last_changed']):<10}", "date", color
        )
        print(f"{name}  {tools}  {branch} {dates}  {summary(r, color)}")
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


def push_candidates(info: dict) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Repos whose current branch has commits to push, and those skipped with a reason.

    Uncommitted changes do not block a push (only commits are sent). A branch that is also behind
    its upstream is skipped: the push would be rejected and needs a pull/rebase first.
    """
    ready, skipped = [], []
    for r in info["repos"]:
        if "error" in r or not r["ahead"]:
            continue
        if r["upstream"] is None:
            skipped.append((r, "no upstream"))
        elif r["behind"]:
            skipped.append((r, f"diverged: {r['behind']} behind {r['upstream']}"))
        else:
            ready.append(r)
    return ready, skipped


def push(info: dict, apply: bool = False) -> bool:
    """Push the current branch of every repo with unpushed commits. Dry run unless apply.

    Returns False if any push failed. git runs attached to the terminal, so SSH passphrase or
    credential prompts still work.
    """
    color = use_color()
    ready, skipped = push_candidates(info)
    for r, reason in skipped:
        print(
            f"skip  {paint(r['repo'], 'repo', color)}  {paint(reason, 'modified', color)}"
        )
    if not ready:
        print("Nothing to push.")
        return True
    failed = []
    for r in ready:
        target = f"{r['ahead']} commit(s) [{r['branch']}] -> {r['upstream']}"
        note = (
            paint("  (has uncommitted changes, not pushed)", "untracked", color)
            if r["dirty"]
            else ""
        )
        if not apply:
            print(f"would push  {paint(r['repo'], 'repo', color)}  {target}{note}")
            continue
        print(f"push  {paint(r['repo'], 'repo', color)}  {target}{note}", flush=True)
        if subprocess.run(["git", "-C", r["repo"], "push"]).returncode != 0:
            failed.append(r["repo"])
    if not apply:
        print(f"\nDry run: {len(ready)} repo(s) would be pushed. Add --apply to push.")
    elif failed:
        print(
            paint(
                f"\n{len(failed)} push(es) failed: " + ", ".join(failed), "error", color
            )
        )
    else:
        print(f"\nPushed {len(ready)} repo(s).")
    return not failed


FETCH_TIMEOUT = (
    60  # seconds per repo; an unreachable remote must not stall the whole run
)


def fetch(repo: str) -> str | None:
    """git fetch the upstream's remote without prompting. Returns an error message, or None."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            ["git", "-C", repo, "fetch", "--quiet"],
            capture_output=True,
            text=True,
            env=env,
            timeout=FETCH_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"fetch timed out after {FETCH_TIMEOUT}s"
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        return "fetch failed: " + (lines[-1] if lines else f"exit {result.returncode}")
    return None


def pull_verdict(r: dict) -> str | None:
    """Why a repo that is behind must not be pulled automatically, or None if it can fast-forward."""
    if r["dirty"]:
        open_ = ", ".join(
            f"{len(r[k])} {k}"
            for k in ("conflicts", "staged", "modified", "untracked")
            if r[k]
        )
        return f"has uncommitted changes ({open_}); commit or stash first"
    if r["ahead"]:
        return f"diverged: {r['ahead']} local and {r['behind']} remote commit(s); merge or rebase by hand"
    return None


def pull(info: dict, apply: bool = False) -> bool:
    """Fast-forward repos that are behind their upstream and have nothing open locally.

    Fetches every repo with an upstream first, so "behind" is current. Never merges, rebases,
    stashes or forces: anything other than a clean fast-forward is reported and left alone.
    Dry run unless apply. Returns False if a fetch or pull failed.
    """
    color = use_color()
    progress = sys.stderr.isatty()
    pullable, blocked, failed, current = [], [], [], 0
    for r in info["repos"]:
        if "error" in r or r["upstream"] is None:
            continue
        if progress:
            print(f"fetch {r['repo']}", end="\r", flush=True, file=sys.stderr)
        error = fetch(r["repo"])
        if progress:
            print(" " * (len(r["repo"]) + 6), end="\r", file=sys.stderr)
        if error:
            failed.append((r, error))
            continue
        r = repo_status(Path(r["repo"]))
        if "error" in r or not r["behind"]:
            current += 1
            continue
        reason = pull_verdict(r)
        (blocked.append((r, reason)) if reason else pullable.append(r))

    for r, reason in blocked:
        print(
            f"leave  {paint(r['repo'], 'repo', color)}  {r['behind']} behind  {paint(reason, 'modified', color)}"
        )
    for r, error in failed:
        print(
            f"error  {paint(r['repo'], 'repo', color)}  {paint(error, 'error', color)}"
        )
    pull_failed = []
    for r in pullable:
        target = f"{r['behind']} commit(s) from {r['upstream']} [{r['branch']}]"
        if not apply:
            print(f"would pull  {paint(r['repo'], 'repo', color)}  {target}")
            continue
        print(f"pull   {paint(r['repo'], 'repo', color)}  {target}", flush=True)
        result = subprocess.run(
            ["git", "-C", r["repo"], "pull", "--ff-only", "--quiet"], check=False
        )
        if result.returncode != 0:
            pull_failed.append(r["repo"])

    print(
        f"\n{current} up to date, {len(pullable)} {'pulled' if apply else 'can fast-forward'},"
        f" {len(blocked)} left alone, {len(failed) + len(pull_failed)} failed"
    )
    if pullable and not apply:
        print(
            "Dry run (remotes were fetched, working trees untouched). Add --apply to pull."
        )
    if pull_failed:
        print(paint("pull failed: " + ", ".join(pull_failed), "error", color))
    return not failed and not pull_failed
