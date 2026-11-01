import argparse

from system import postgres, projects, pyversions, space
from system.cleanup import cleanup
from system.collect import collect, save


def main() -> None:
    parser = argparse.ArgumentParser(prog="system")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("collect", help="save system info to reports/<hostname>/system-info.json")

    sub.add_parser("postgres", help="PostgreSQL versions (installed vs. running), clusters, databases and sizes")

    py = sub.add_parser("pyversions", help="audit .python-version files, venv interpreters and uv Pythons")
    py.add_argument("--root", action="append", help="project root to scan (repeatable; default: /srv/python and ~/python)")
    py.add_argument("--target", default=pyversions.DEFAULT_TARGET, help="wanted Python minor version (default: %(default)s)")

    sp = sub.add_parser("space", help="show where disk space goes and what is easy to reclaim")
    sp.add_argument("--root", action="append", help="project root whose venvs to measure (repeatable)")

    pr = sub.add_parser("projects", help="classify projects, flag stale ones, audit dependencies, archive")
    pr.add_argument("--root", action="append", help="project root to scan (default: roots of this host in projects.toml)")
    pr.add_argument("--audit", action="store_true", help="run uv audit on active/grey projects and unclassified ones with a .venv")
    pr.add_argument("--archive", action="store_true", help="create tar.gz for projects marked 'archive' (originals are kept)")
    pr.add_argument("--apply", action="store_true", help="remove the .venv of 'idea' and 'archive' projects")

    clean = sub.add_parser("cleanup", help="prune the uv cache and delete old logs (dry run by default)")
    clean.add_argument("--apply", action="store_true", help="actually delete; default is a dry run")
    clean.add_argument("--days", type=int, default=30, help="keep logs and journal newer than this (default: 30)")
    clean.add_argument("--skip-uv", action="store_true", help="leave the uv cache alone")
    clean.add_argument("--skip-logs", action="store_true", help="leave /var/log and the journal alone")

    args = parser.parse_args()
    if args.command == "collect":
        print(f"Saved system info to {save(collect())}")
    elif args.command == "postgres":
        info = postgres.check()
        postgres.show(info)
        print(f"\nSaved to {save(info, 'postgres.json')}")
    elif args.command == "pyversions":
        info = pyversions.audit(args.root or pyversions.DEFAULT_ROOTS, args.target)
        pyversions.show(info)
        print(f"\nSaved to {save(info, 'pyversions.json')}")
    elif args.command == "projects":
        info = projects.survey(args.root, audit=args.audit)
        projects.show(info)
        if args.archive:
            print()
            projects.archive_pending(info)
        if args.apply:
            print()
            projects.apply_venv_removal(info)
        print(f"\nSaved to {save(info, 'projects.json')}")
    elif args.command == "space":
        info = space.survey(args.root or pyversions.DEFAULT_ROOTS)
        space.show(info)
        print(f"\nSaved to {save(info, 'space.json')}")
    else:
        cleanup(days=args.days, apply=args.apply, uv=not args.skip_uv, logs=not args.skip_logs)
