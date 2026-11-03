"""Check PostgreSQL: installed vs. running versions, clusters, databases and their sizes."""

import csv
import io
import os
import re
import shutil
import socket
import subprocess
from datetime import datetime
from pathlib import Path

from system.util import human

SOCKET_DIR = "/var/run/postgresql"
PG_LIB = Path("/usr/lib/postgresql")

DATABASES_SQL = """
select datname as name,
       case when has_database_privilege(datname, 'CONNECT') then pg_database_size(datname) end as size_bytes,
       pg_get_userbyid(datdba) as owner,
       pg_encoding_to_char(encoding) as encoding,
       datistemplate as is_template,
       current_setting('server_version') as server_version
from pg_database
order by size_bytes desc nulls last, name
"""


def installed_servers() -> dict[str, str]:
    """Major version -> package version for installed postgresql-<major> server packages."""
    out = subprocess.run(
        [
            "dpkg-query",
            "-W",
            "-f",
            "${db:Status-Abbrev} ${Package} ${Version}\\n",
            "postgresql-[0-9]*",
        ],
        capture_output=True,
        text=True,
    ).stdout
    servers = {}
    for line in out.splitlines():
        status, _, rest = line.partition(" ")
        package, _, version = rest.strip().partition(" ")
        match = re.fullmatch(r"postgresql-(\d+)", package)
        if match and status == "ii" and version:
            servers[match[1]] = version
    return servers


def client_version() -> str | None:
    if shutil.which("psql") is None:
        return None
    out = subprocess.run(["psql", "--version"], capture_output=True, text=True).stdout
    match = re.search(r"\d+(\.\d+)*", out)
    return match[0] if match else None


def list_clusters() -> list[dict]:
    if shutil.which("pg_lsclusters") is None:
        return []
    out = subprocess.run(
        ["pg_lsclusters", "--no-header"], capture_output=True, text=True
    ).stdout
    clusters = []
    for line in out.splitlines():
        version, name, port, status, owner, data_dir, *log = line.split(None, 6)
        clusters.append(
            {
                "version": version,
                "name": name,
                "port": int(port),
                "status": status,
                "owner": owner,
                "data_dir": data_dir,
            }
        )
    return clusters


def psql_binary(version: str) -> str:
    binary = PG_LIB / version / "bin" / "psql"
    return str(binary) if binary.exists() else "psql"


def run_query(cluster: dict, sql: str) -> tuple[list[dict], str]:
    """Run sql against a cluster; returns (rows, how we connected).

    Tries the current user first, then falls back to `sudo -u <cluster owner>`
    (peer authentication), which prompts for a password on a terminal.
    Raises RuntimeError with psql's message if both fail.
    """
    psql = [
        psql_binary(cluster["version"]),
        "-X",
        "-w",
        "--csv",
        "-h",
        SOCKET_DIR,
        "-p",
        str(cluster["port"]),
    ]
    psql += ["-d", "postgres", "-c", sql]
    attempts = [("current user", psql)]
    if os.geteuid() != 0:
        attempts.append(
            (f"sudo -u {cluster['owner']}", ["sudo", "-u", cluster["owner"], *psql])
        )

    errors = []
    for how, cmd in attempts:
        # cwd="/" so the postgres user does not trip over an unreadable working directory.
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd="/", timeout=120
        )
        if result.returncode == 0:
            return list(csv.DictReader(io.StringIO(result.stdout))), how
        message = (
            result.stderr.strip().splitlines()[-1]
            if result.stderr.strip()
            else f"exit {result.returncode}"
        )
        errors.append(f"{how}: {message}")
    raise RuntimeError("; ".join(errors))


def inspect_cluster(cluster: dict) -> dict:
    info = {**cluster}
    if not cluster["status"].startswith("online"):
        return info
    try:
        rows, how = run_query(cluster, DATABASES_SQL)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        info["error"] = str(exc) or "timed out"
        return info
    info["connected_as"] = how
    info["running_version"] = rows[0]["server_version"].split()[0] if rows else None
    info["databases"] = [
        {
            "name": row["name"],
            "size_bytes": int(row["size_bytes"]) if row["size_bytes"] else None,
            "owner": row["owner"],
            "encoding": row["encoding"],
            "is_template": row["is_template"] == "t",
        }
        for row in rows
    ]
    return info


def findings(installed: dict[str, str], clusters: list[dict]) -> list[str]:
    notes = []
    used = {c["version"] for c in clusters}
    newest = max(installed, key=int, default=None)
    for version in sorted(installed, key=int):
        if version not in used:
            notes.append(
                f"PostgreSQL {version} is installed but has no cluster (unused)"
            )
    for c in clusters:
        label = f"{c['version']}/{c['name']}"
        if not c["status"].startswith("online"):
            notes.append(f"cluster {label} is {c['status']}")
        if newest and int(c["version"]) < int(newest):
            notes.append(
                f"cluster {label} runs {c['version']} but {newest} is installed "
                f"(migrate with: sudo pg_upgradecluster {c['version']} {c['name']})"
            )
        running = c.get("running_version")
        package = installed.get(c["version"], "").split("-")[0]
        if running and package and running != package:
            notes.append(
                f"cluster {label} runs {running} but package is {package}: restart needed"
            )
        if "error" in c:
            notes.append(f"cluster {label}: could not query databases ({c['error']})")
    return notes


def check() -> dict:
    installed = installed_servers()
    clusters = [inspect_cluster(c) for c in list_clusters()]
    return {
        "hostname": socket.gethostname(),
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "client_version": client_version(),
        "installed_servers": installed,
        "clusters": clusters,
        "findings": findings(installed, clusters),
    }


def show(info: dict) -> None:
    print(f"psql client: {info['client_version'] or 'not installed'}")
    print("\nInstalled servers:")
    for version, package in sorted(
        info["installed_servers"].items(), key=lambda kv: int(kv[0])
    ):
        print(f"  postgresql-{version}  {package}")
    if not info["installed_servers"]:
        print("  none")

    print("\nClusters:")
    if not info["clusters"]:
        print("  none")
    for c in info["clusters"]:
        running = c.get("running_version")
        print(
            f"  {c['version']}/{c['name']}  port {c['port']}  {c['status']}  {c['data_dir']}"
        )
        if running:
            print(
                f"    running version: {running}  (connected via {c['connected_as']})"
            )
        if "error" in c:
            print(f"    cannot query: {c['error']}")
        dbs = c.get("databases", [])
        for db in dbs:
            size = human(db["size_bytes"]) if db["size_bytes"] is not None else "n/a"
            flag = "  [template]" if db["is_template"] else ""
            print(
                f"    {db['name']:<24} {size:>10}  owner={db['owner']}  {db['encoding']}{flag}"
            )
        if dbs:
            total = sum(db["size_bytes"] or 0 for db in dbs)
            print(f"    {'total':<24} {human(total):>10}")

    print("\nFindings:")
    for note in info["findings"] or ["none"]:
        print(f"  - {note}")
