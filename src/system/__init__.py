import argparse

from system.cleanup import cleanup
from system.collect import collect, save
from system.postgres import check, show


def main() -> None:
    parser = argparse.ArgumentParser(prog="system")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("collect", help="save system info to reports/<hostname>/system-info.json")

    sub.add_parser("postgres", help="PostgreSQL versions (installed vs. running), clusters, databases and sizes")

    clean = sub.add_parser("cleanup", help="prune the uv cache and delete old logs (dry run by default)")
    clean.add_argument("--apply", action="store_true", help="actually delete; default is a dry run")
    clean.add_argument("--days", type=int, default=30, help="keep logs and journal newer than this (default: 30)")
    clean.add_argument("--skip-uv", action="store_true", help="leave the uv cache alone")
    clean.add_argument("--skip-logs", action="store_true", help="leave /var/log and the journal alone")

    args = parser.parse_args()
    if args.command == "collect":
        print(f"Saved system info to {save(collect())}")
    elif args.command == "postgres":
        info = check()
        show(info)
        print(f"\nSaved to {save(info, 'postgres.json')}")
    else:
        cleanup(days=args.days, apply=args.apply, uv=not args.skip_uv, logs=not args.skip_logs)
