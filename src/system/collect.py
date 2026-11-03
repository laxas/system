"""Collect basic system information and save it under reports/<hostname>/."""

import json
import os
import platform
import re
import shutil
import socket
import subprocess
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "reports"


def run(*cmd: str, ok_nonzero: bool = False) -> str | None:
    """Run a command with a stable C locale; return stdout or None on any failure."""
    if shutil.which(cmd[0]) is None:
        return None
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    return result.stdout.strip() if (result.returncode == 0 or ok_nonzero) else None


def run_json(*cmd: str):
    out = run(*cmd)
    try:
        return json.loads(out) if out else None
    except json.JSONDecodeError:
        return None


def read_kv_file(path: str, sep: str) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return data
    for line in lines:
        key, found, value = line.partition(sep)
        if found:
            data[key.strip()] = value.strip().strip('"')
    return data


def os_info() -> dict:
    osr = read_kv_file("/etc/os-release", "=")
    return {
        "pretty_name": osr.get("PRETTY_NAME"),
        "version": osr.get("VERSION"),
        "codename": osr.get("VERSION_CODENAME"),
        "kernel": platform.release(),
        "kernel_version": platform.version(),
        "architecture": platform.machine(),
    }


def cpu_info() -> dict:
    cpuinfo = read_kv_file("/proc/cpuinfo", ":")  # last processor wins; fine for model
    return {
        "model": cpuinfo.get("model name"),
        "logical_cpus": os.cpu_count(),
        "load_avg": os.getloadavg(),
    }


def memory_info() -> dict:
    meminfo = read_kv_file("/proc/meminfo", ":")

    def mib(key: str) -> int | None:
        match = re.match(r"(\d+)\s*kB", meminfo.get(key, ""))
        return int(match[1]) // 1024 if match else None

    return {
        "total_mib": mib("MemTotal"),
        "available_mib": mib("MemAvailable"),
        "swap_total_mib": mib("SwapTotal"),
        "swap_free_mib": mib("SwapFree"),
    }


def disk_info() -> dict:
    return {
        "block_devices": (
            run_json(
                "lsblk", "-J", "-e7", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL"
            )
            or {}
        ).get("blockdevices"),
        "filesystems": [
            fs for device, mount in _real_mounts() if (fs := _fs_usage(device, mount))
        ],
    }


def _real_mounts() -> list[tuple[str, str]]:
    """(device, mountpoint) for block-device-backed mounts, first mount per device."""
    mounts = []
    seen = set()
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return mounts
    for line in lines:
        device, mount, *_ = line.split()
        if (
            device.startswith("/dev/")
            and not device.startswith("/dev/loop")
            and device not in seen
        ):
            seen.add(device)
            mounts.append((device, mount.replace("\\040", " ")))
    return mounts


def _fs_usage(device: str, mount: str) -> dict | None:
    try:
        st = os.statvfs(mount)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    return {
        "mount": mount,
        "device": device,
        "total_gib": round(total / 2**30, 1),
        "used_gib": round(used / 2**30, 1),
        "avail_gib": round(avail / 2**30, 1),
        # Same definition as df: reserved blocks count as used.
        "used_percent": round(used / (used + avail) * 100, 1) if used + avail else None,
    }


def network_info() -> dict:
    interfaces = []
    for iface in run_json("ip", "-j", "addr") or []:
        interfaces.append(
            {
                "name": iface.get("ifname"),
                "state": iface.get("operstate"),
                "addresses": [
                    f"{a['local']}/{a['prefixlen']}" for a in iface.get("addr_info", [])
                ],
            }
        )
    return {
        "interfaces": interfaces,
        "default_routes": run_json("ip", "-j", "route", "show", "default"),
        "dns": run("resolvectl", "dns"),
    }


def services_info() -> dict:
    failed = run("systemctl", "--failed", "--no-legend", "--plain", "--no-pager")
    return {
        "system_state": run("systemctl", "is-system-running", ok_nonzero=True),
        "failed_units": [line.split()[0] for line in failed.splitlines()]
        if failed
        else [],
    }


def collect() -> dict:
    return {
        "hostname": socket.gethostname(),
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "virtualization": run("systemd-detect-virt"),
        "hardware": {
            "vendor": run("cat", "/sys/class/dmi/id/sys_vendor"),
            "product": run("cat", "/sys/class/dmi/id/product_name"),
            "bios_version": run("cat", "/sys/class/dmi/id/bios_version"),
        },
        "os": os_info(),
        "uptime": run("uptime", "-p"),
        "cpu": cpu_info(),
        "memory": memory_info(),
        "disk": disk_info(),
        "network": network_info(),
        "services": services_info(),
        "python": platform.python_version(),
    }


def save(info: dict, name: str = "system-info.json") -> Path:
    # Hostname comes from the system, but keep it path-safe regardless.
    host = re.sub(r"[^A-Za-z0-9._-]", "_", info["hostname"])
    path = REPORTS_DIR / host / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    return path
