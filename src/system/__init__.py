import argparse

RAW = argparse.RawDescriptionHelpFormatter

EXAMPLES = {
    "main": """\
examples:
  system collect                     system info -> reports/<host>/system-info.json
  system repos --sort changed        git repos with uncommitted work, stalest first
  system projects --audit            classify projects and audit their dependencies
  system cleanup                     dry run: what would be freed
  system <command> --help            examples for one command

Run cleanup as .venv/bin/system (or make cleanup), not uv run: uv run holds the uv cache lock.""",
    "pyversions": """\
examples:
  system pyversions                          scan /srv/python and ~/python against 3.14
  system pyversions --root ~/python          only one root
  system pyversions --target 3.13            compare against another minor version""",
    "space": """\
examples:
  system space                               biggest reclaimable items
  system space --root /srv/www               also measure venvs under another root""",
    "projects": """\
Projects are classified in projects.toml under [hosts.<hostname>.projects]:
  "~/python/services" = "archive"            # active | grey | idea | archive

examples:
  system projects                            classification, stale locks, backup risks
  system projects --audit                    plus uv audit (needs network, ~30 s)
  system projects --root ~/python            only one root

  # archive one or more projects directly
  system projects --archive ~/python/services        tar.gz to ~/archive (+ .sha256), original kept
  system projects --archive ~/python/a ~/python/b --apply   same, and remove their .venv

  # or mark them "archive" in projects.toml, then
  system projects --archive                  archive every marked project not archived yet
  system projects --archive --apply          same, and remove their .venv
  system projects --apply                    only remove .venv of idea/archive projects

Archives leave out .venv, node_modules and __pycache__. Deleting the original is up to you.""",
    "repos": """\
examples:
  system repos                               repos with uncommitted/unpushed work or no remote
  system repos --files                       plus the changed paths (S staged, M modified, ? untracked)
  system repos --all --sort commit           every repo, oldest last commit first
  system repos --root /srv/python            only one root
  NO_COLOR=1 system repos                    no colors""",
    "cleanup": """\
examples:
  .venv/bin/system cleanup                   dry run: show what would be removed
  .venv/bin/system cleanup --apply           do it (uses sudo for logs, journal, snaps)
  .venv/bin/system cleanup --apply --skip-logs --days 60
  make cleanup                               same as cleanup --apply""",
}

from system import postgres, projects, pyversions, repos, space
from system.cleanup import cleanup
from system.collect import collect, save


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="system", epilog=EXAMPLES["main"], formatter_class=RAW
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "collect", help="save system info to reports/<hostname>/system-info.json"
    )

    sub.add_parser(
        "postgres",
        help="PostgreSQL versions (installed vs. running), clusters, databases and sizes",
    )

    py = sub.add_parser(
        "pyversions",
        epilog=EXAMPLES["pyversions"],
        formatter_class=RAW,
        help="audit .python-version files, venv interpreters and uv Pythons",
    )
    py.add_argument(
        "--root",
        action="append",
        help="project root to scan (repeatable; default: /srv/python and ~/python)",
    )
    py.add_argument(
        "--target",
        default=pyversions.DEFAULT_TARGET,
        help="wanted Python minor version (default: %(default)s)",
    )

    sp = sub.add_parser(
        "space",
        epilog=EXAMPLES["space"],
        formatter_class=RAW,
        help="show where disk space goes and what is easy to reclaim",
    )
    sp.add_argument(
        "--root",
        action="append",
        help="project root whose venvs to measure (repeatable)",
    )

    pr = sub.add_parser(
        "projects",
        epilog=EXAMPLES["projects"],
        formatter_class=RAW,
        help="classify projects, flag stale ones, audit dependencies, archive",
    )
    pr.add_argument(
        "--root",
        action="append",
        help="project root to scan (default: roots of this host in projects.toml)",
    )
    pr.add_argument(
        "--audit",
        action="store_true",
        help="run uv audit on active/grey projects and unclassified ones with a .venv",
    )
    pr.add_argument(
        "--archive",
        nargs="*",
        metavar="PATH",
        help="create tar.gz for projects marked 'archive' (originals are kept);"
        " with PATHs, archive just those projects, whatever their status",
    )
    pr.add_argument(
        "--apply",
        action="store_true",
        help="remove the .venv of 'idea' and 'archive' projects (with --archive PATH: of those)",
    )

    rp = sub.add_parser(
        "repos",
        epilog=EXAMPLES["repos"],
        formatter_class=RAW,
        help="git repos with uncommitted changes or unpushed commits",
    )
    rp.add_argument(
        "--root",
        action="append",
        help="directory whose subdirectories to scan (repeatable; default: /srv/python and ~/python)",
    )
    rp.add_argument("--files", action="store_true", help="list the changed files")
    rp.add_argument("--all", action="store_true", help="also list clean repos")
    rp.add_argument(
        "--sort",
        choices=repos.SORT_KEYS,
        default="path",
        help="order by path, or oldest first by last commit / last changed file",
    )

    clean = sub.add_parser(
        "cleanup",
        epilog=EXAMPLES["cleanup"],
        formatter_class=RAW,
        help="prune the uv cache and delete old logs (dry run by default)",
    )
    clean.add_argument(
        "--apply", action="store_true", help="actually delete; default is a dry run"
    )
    clean.add_argument(
        "--days",
        type=int,
        default=30,
        help="keep logs and journal newer than this (default: 30)",
    )
    clean.add_argument(
        "--skip-uv", action="store_true", help="leave the uv cache alone"
    )
    clean.add_argument(
        "--skip-snap", action="store_true", help="leave old snap revisions alone"
    )
    clean.add_argument(
        "--skip-logs", action="store_true", help="leave /var/log and the journal alone"
    )

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
    elif args.command == "projects" and args.archive:
        projects.archive_paths(args.archive, remove_venvs=args.apply)
    elif args.command == "projects":
        info = projects.survey(args.root, audit=args.audit)
        projects.show(info)
        if args.archive is not None:  # bare --archive: everything marked "archive"
            print()
            projects.archive_pending(info)
        if args.apply:
            print()
            projects.apply_venv_removal(info)
        print(f"\nSaved to {save(info, 'projects.json')}")
    elif args.command == "repos":
        info = repos.survey(args.root or pyversions.DEFAULT_ROOTS)
        repos.show(info, files=args.files, show_all=args.all, sort=args.sort)
        print(f"\nSaved to {save(info, 'repos.json')}")
    elif args.command == "space":
        info = space.survey(args.root or pyversions.DEFAULT_ROOTS)
        space.show(info)
        print(f"\nSaved to {save(info, 'space.json')}")
    else:
        cleanup(
            days=args.days,
            apply=args.apply,
            uv=not args.skip_uv,
            logs=not args.skip_logs,
            snap=not args.skip_snap,
        )
