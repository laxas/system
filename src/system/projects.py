"""Project registry: classify projects, flag stale ones, audit dependencies, archive.

Statuses live in projects.toml (per host, keyed by project path):
  active   developed or deployed; dependencies are kept fresh and audited
  grey     not developed, but may still run or be revived; audited
  idea     old idea, not worth running; should not have a .venv
  archive  to be archived as tar.gz
Projects missing from the registry are 'unclassified'. They are audited if they have a .venv.

Screening only reports. Audit findings are never fixed here: upgrading dependencies needs tests
and judgement, so it is done per project in a separate session. The only changes this module can
make are the explicit --archive (writes tar.gz, keeps originals) and --apply (removes .venv of
idea/archive projects).
"""

import hashlib
import json
import socket
import subprocess
import tarfile
import time
import tomllib
from datetime import datetime
from pathlib import Path

from system.collect import REPO_ROOT
from system.pyversions import DEFAULT_ROOTS, find_projects, processes_in, version_key
from system.space import dir_size
from system.util import human

REGISTRY = REPO_ROOT / "projects.toml"
STATUSES = ("active", "grey", "idea", "archive")
LOCK_MAX_AGE_DAYS = {"active": 90, "grey": 180}  # override per host with [hosts.<h>.lock_max_age_days]
SKIP_IN_ARCHIVE = {".venv", "node_modules", "__pycache__"}


def load_registry(host: str) -> dict:
    try:
        data = tomllib.loads(REGISTRY.read_text())
    except FileNotFoundError:
        data = {}
    cfg = data.get("hosts", {}).get(host, {})
    projects = {str(Path(k).expanduser()): v for k, v in cfg.get("projects", {}).items()}
    unknown = {k: v for k, v in projects.items() if v not in STATUSES}
    if unknown:
        raise SystemExit(f"{REGISTRY}: status must be one of {', '.join(STATUSES)}; got {unknown}")
    return {
        "roots": cfg.get("roots", DEFAULT_ROOTS),
        "archive_dir": Path(cfg.get("archive_dir", "~/archive")).expanduser(),
        "lock_max_age_days": {**LOCK_MAX_AGE_DAYS, **cfg.get("lock_max_age_days", {})},
        "projects": projects,
    }


def git(project: Path, *args: str) -> str | None:
    result = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def age_days(timestamp: str | float | None) -> int | None:
    return None if timestamp is None else int((time.time() - float(timestamp)) / 86400)


def signals(project: Path) -> dict:
    """Facts about a project; only trust git if the project is its own repository."""
    has_git = (project / ".git").exists()
    last = git(project, "log", "-1", "--format=%ct") if has_git else None
    lock = next((f for f in ("uv.lock", "poetry.lock") if (project / f).exists()), None)
    lock_ts = git(project, "log", "-1", "--format=%ct", "--", lock) if has_git and lock else None
    if lock and not lock_ts:
        lock_ts = str((project / lock).stat().st_mtime)  # not committed: fall back to file mtime
    ahead = git(project, "rev-list", "--count", "@{u}..HEAD") if last else None
    return {
        "has_git": has_git,
        "has_commits": last is not None,
        "commit_age_days": age_days(last),
        "lock_file": lock,
        "lock_age_days": age_days(lock_ts),
        "uncommitted": len((git(project, "status", "--porcelain") or "").splitlines()) if has_git else None,
        "has_remote": bool(git(project, "remote")) if has_git else False,
        "unpushed": int(ahead) if ahead else 0,
        "venv_bytes": dir_size(project / ".venv"),
    }


def archive_slug(project: Path) -> str:
    return f"{project.parent.name}-{project.name}".lstrip("_.")


def existing_archives(project: Path, archive_dir: Path) -> list[Path]:
    return sorted(archive_dir.glob(f"{archive_slug(project)}-*.tar.gz")) if archive_dir.is_dir() else []


def should_audit(status: str | None, row: dict) -> bool:
    if status in ("active", "grey"):
        return True
    return status is None and row["venv_bytes"] is not None


def advise(status: str | None, row: dict, limit: dict[str, int], archived: bool, running: list[int]) -> list[str]:
    notes = []
    if status in ("active", "grey") and row["lock_age_days"] is not None and row["lock_age_days"] > limit[status]:
        notes.append(f"{row['lock_file']} is {row['lock_age_days']} days old (limit {limit[status]})")
    if status in ("active", "grey") and row["lock_file"] == "poetry.lock":
        notes.append("poetry project: uv audit cannot check it, migrate to uv")
    if status in ("idea", "archive") and row["venv_bytes"] is not None:
        notes.append(f"remove .venv ({human(row['venv_bytes'])})")
    if status == "archive" and not archived:
        notes.append("archive not created yet (--archive)")
    if running:
        notes.append(f"running from its venv (pids {running})")
    # Backup risks matter for anything we might delete or rely on.
    if not row["has_git"]:
        notes.append("no git: nothing but file sync/backup protects this code")
    elif not row["has_commits"]:
        notes.append("git repo without commits")
    elif not row["has_remote"]:
        notes.append("no remote: commits exist only here")
    if row["uncommitted"]:
        notes.append(f"{row['uncommitted']} uncommitted files")
    if row["unpushed"]:
        notes.append(f"{row['unpushed']} unpushed commits")
    return notes


def fixed_in(vulns: list[dict], installed: str) -> tuple[str | None, int]:
    """(version fixing every fixable vulnerability, number of vulnerabilities with no fix).

    fix_versions lists one fix per release branch (e.g. 5.2.17 and 6.0.8), so for each
    vulnerability take the fix on the installed branch, else the lowest newer fix.
    """
    have = version_key(installed)
    needed, unfixed = [], 0
    for v in vulns:
        newer = [f for f in v.get("fix_versions") or [] if version_key(f) > have]
        same_branch = [f for f in newer if version_key(f)[:2] == have[:2]]
        if newer:
            needed.append(min(same_branch or newer, key=version_key))
        else:
            unfixed += 1
    return (max(needed, key=version_key) if needed else None), unfixed


def describe_fix(pkg: dict) -> str:
    text = f"{pkg['name']} {pkg['version']}"
    if pkg["upgrade_to"]:
        text += f" -> {pkg['upgrade_to']}"
    if pkg["unfixed"]:
        text += f" ({pkg['unfixed']} unfixed)" if pkg["upgrade_to"] else " (no fix)"
    return text


def audit_project(project: Path) -> dict:
    if not (project / "uv.lock").exists():
        return {"status": "skipped", "reason": "no uv.lock"}
    try:
        result = subprocess.run(
            ["uv", "audit", "--frozen", "--output-format", "json"],
            cwd=project, capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "reason": str(exc) or "timed out"}
    try:
        data = json.loads(result.stdout)  # exit code 1 just means vulnerabilities were found
    except json.JSONDecodeError:
        return {"status": "error", "reason": (result.stderr.strip().splitlines() or ["no output"])[-1]}
    by_package: dict[tuple[str, str], list[dict]] = {}
    for v in data.get("vulnerabilities", []):
        by_package.setdefault((v["dependency"]["name"], v["dependency"]["version"]), []).append(v)
    return {
        "status": "vulnerable" if by_package else "clean",
        "vulnerabilities": sum(len(v) for v in by_package.values()),
        "packages": [
            {"name": n, "version": ver, "count": len(vs), **dict(zip(("upgrade_to", "unfixed"), fixed_in(vs, ver)))}
            for (n, ver), vs in sorted(by_package.items())
        ],
    }


def create_archive(project: Path, archive_dir: Path) -> Path:
    """tar.gz of the project (including .git, excluding venvs/caches); verified before it is kept."""
    archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)  # archives can hold credentials: owner only
    dest = archive_dir / f"{archive_slug(project)}-{datetime.now():%Y%m%d}.tar.gz"
    part = dest.with_name(dest.name + ".part")
    try:
        with tarfile.open(part, "w:gz") as tar:
            tar.add(project, arcname=project.name, filter=lambda t: None if SKIP_IN_ARCHIVE & set(Path(t.name).parts) else t)
        with tarfile.open(part, "r:gz") as tar:  # reading it back end to end proves it is not truncated
            members = tar.getmembers()
            for m in members:
                if m.isfile():
                    tar.extractfile(m).read()
    except (OSError, tarfile.TarError):
        part.unlink(missing_ok=True)
        raise
    part.chmod(0o600)
    part.rename(dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    checksum = dest.with_name(dest.name + ".sha256")
    checksum.write_text(f"{digest}  {dest.name}\n")
    checksum.chmod(0o600)
    return dest


def survey(roots: list[str] | None, audit: bool) -> dict:
    host = socket.gethostname()
    reg = load_registry(host)
    found = find_projects(roots or reg["roots"])
    running = processes_in({p: (p / ".venv").resolve() for p in found})
    rows = []
    for project in found:
        status = reg["projects"].get(str(project))
        row = {"project": str(project), "status": status or "unclassified", **signals(project)}
        row["archives"] = [str(a) for a in existing_archives(project, reg["archive_dir"])]
        row["running_pids"] = running.get(project, [])
        row["notes"] = advise(status, row, reg["lock_max_age_days"], bool(row["archives"]), row["running_pids"])
        if audit and should_audit(status, row):
            row["audit"] = audit_project(project)
            result = row["audit"]
            if result["status"] == "vulnerable":
                pkgs = ", ".join(describe_fix(p) for p in result["packages"])
                row["notes"].append(f"audit: {result['vulnerabilities']} vulnerabilities in {pkgs}")
            elif result["status"] == "error":
                row["notes"].append(f"audit failed: {result['reason']}")
            elif result["status"] == "skipped" and status in ("active", "grey"):
                row["notes"].append(f"audit skipped: {result['reason']}")
        rows.append(row)
    missing = [p for p in reg["projects"] if p not in {str(f) for f in found}]
    order = {s: i for i, s in enumerate(("active", "grey", "unclassified", "idea", "archive"))}
    rows.sort(key=lambda r: (order[r["status"]], -(r["commit_age_days"] or 0), r["project"]))
    return {
        "hostname": host,
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "audited": audit,
        "archive_dir": str(reg["archive_dir"]),
        "registered_but_missing": missing,
        "projects": rows,
    }


def apply_venv_removal(info: dict) -> None:
    """Delete .venv of idea/archive projects (never one a process runs from)."""
    for row in info["projects"]:
        venv = Path(row["project"]) / ".venv"
        if row["status"] not in ("idea", "archive") or not venv.exists():
            continue
        if row["running_pids"]:
            print(f"skip {venv}: in use by pids {row['running_pids']}")
        elif not (venv / "pyvenv.cfg").exists() or venv.is_symlink():
            print(f"skip {venv}: does not look like a venv")
        else:
            subprocess.run(["rm", "-rf", "--", str(venv)], check=True)
            print(f"removed {venv} ({human(row['venv_bytes'] or 0)})")


def archive_pending(info: dict) -> None:
    for row in info["projects"]:
        if row["status"] != "archive" or row["archives"]:
            continue
        project = Path(row["project"])
        try:
            dest = create_archive(project, Path(info["archive_dir"]))
        except (OSError, tarfile.TarError) as exc:
            print(f"FAILED {project}: {exc}")
            continue
        print(f"archived {project} -> {dest} ({human(dest.stat().st_size)}); original left in place")


def show(info: dict) -> None:
    age = lambda v: "-" if v is None else str(v)  # noqa: E731
    width = max((len(r["project"]) for r in info["projects"]), default=10)
    print(f"{'status':<13}{'project':<{width}}  {'commit_d':>8} {'lock_d':>7} {'venv':>10}")
    for r in info["projects"]:
        venv = human(r["venv_bytes"]) if r["venv_bytes"] is not None else "-"
        print(f"{r['status']:<13}{r['project']:<{width}}  {age(r['commit_age_days']):>8} {age(r['lock_age_days']):>7} {venv:>10}")
    unclassified = sum(r["status"] == "unclassified" for r in info["projects"])
    if unclassified:
        print(f"\n{unclassified} unclassified projects: add them to projects.toml")
    print("\nNeeds attention:")
    flagged = [r for r in info["projects"] if r["notes"]]
    for r in flagged:
        print(f"  [{r['status']}] {r['project']}")
        for note in r["notes"]:
            print(f"    - {note}")
    for path in info["registered_but_missing"]:
        print(f"  registered but not found on disk: {path}")
    if not info["audited"]:
        print("\n(dependency audit not run: use --audit)")
