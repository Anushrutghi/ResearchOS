"""Command-line interface for ResearchOS."""

import argparse
import json
import sys

from researchos import __version__
from researchos.core.orchestrator import run_research
from researchos.core.request import create_research_request
from researchos.web import serve
from researchos.security.auth import create_user, revoke_user
from researchos.maintenance import create_backup, restore_backup, verify_backup, scientific_quality_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="researchos", description="Evidence-first scientific research workflow foundation")
    parser.add_argument("--version", action="version", version=f"ResearchOS {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    request = commands.add_parser("request", help="validate and display a research request")
    run = commands.add_parser("run", help="create a literature discovery brief and local run record")
    for subcommand in (request, run):
        subcommand.add_argument("--topic", required=True, help="research question or topic")
        subcommand.add_argument("--field", required=True, dest="research_field", help="scientific discipline")
        subcommand.add_argument("--format", default="markdown", dest="output_format", help="research brief format (currently markdown); approved writing drafts support additional exporters")
    run.add_argument("--output-dir", default="research_runs", help="directory in which to save the run")
    run.add_argument("--max-papers", type=int, default=10, help="maximum results per literature provider (1-100)")
    run.add_argument("--no-search", action="store_true", help="skip online literature discovery")
    web = commands.add_parser("web", help="start the local browser interface")
    web.add_argument("--host", default="127.0.0.1", help="interface to bind (default: localhost)")
    web.add_argument("--port", type=int, default=8000, help="port to listen on")
    web.add_argument("--output-dir", default="research_runs", help="directory in which to save runs")
    backup = commands.add_parser("backup", help="create a verified archive of local ResearchOS data")
    backup.add_argument("--output-dir", default="research_runs")
    backup.add_argument("--destination", required=True, help="archive path outside the output directory")
    verify = commands.add_parser("verify-backup", help="verify a ResearchOS backup without restoring it")
    verify.add_argument("archive", help="backup archive to verify")
    restore = commands.add_parser("restore", help="restore a verified backup into a new directory")
    restore.add_argument("archive", help="backup archive to restore")
    restore.add_argument("--destination", required=True, help="new, previously unused output directory")
    quality = commands.add_parser("quality", help="audit local record integrity and review state")
    quality.add_argument("--output-dir", default="research_runs")
    auth = commands.add_parser("auth", help="manage local ResearchOS API users")
    auth_commands = auth.add_subparsers(dest="auth_action", required=True)
    create = auth_commands.add_parser("create-user", help="create a user and one-time bearer token")
    create.add_argument("--output-dir", default="research_runs")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--admin", action="store_true", help="grant administrator permission (first user is always administrator)")
    revoke = auth_commands.add_parser("revoke-user", help="disable a user's API token")
    revoke.add_argument("--output-dir", default="research_runs")
    revoke.add_argument("--username", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "auth":
        try:
            if args.auth_action == "create-user":
                user, token = create_user(args.output_dir, args.username, args.display_name, args.admin)
                print(json.dumps({"user": user, "api_token": token}, indent=2))
                print("Store this token securely; it will not be shown again.", file=sys.stderr)
            else:
                print(json.dumps({"user": revoke_user(args.output_dir, args.username)}, indent=2))
        except (ValueError, RuntimeError, OSError, FileNotFoundError) as exc:
            print(f"researchos: error: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command in {"backup", "verify-backup", "restore"}:
        try:
            if args.command == "backup":
                result = create_backup(args.output_dir, args.destination)
            elif args.command == "verify-backup":
                result = verify_backup(args.archive)
            else:
                result = restore_backup(args.archive, args.destination)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"researchos: error: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "quality":
        try:
            print(json.dumps(scientific_quality_report(args.output_dir), indent=2, ensure_ascii=False))
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"researchos: error: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "web":
        try:
            serve(host=args.host, port=args.port, output_dir=args.output_dir)
        except OSError as exc:
            print(f"researchos: error: {exc}", file=sys.stderr)
            return 2
        return 0
    try:
        request = create_research_request(args.topic, args.research_field, args.output_format)
        request_data = request.to_dict()
        if args.command == "request":
            print(json.dumps(request_data, indent=2, ensure_ascii=False))
            return 0
        run = run_research(request, output_dir=args.output_dir, max_papers=args.max_papers, search=not args.no_search)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"researchos: error: {exc}", file=sys.stderr)
        return 2
    print(f"Research run saved: {run.directory}")
    print(f"Brief: {run.report_path}")
    print(f"Candidate sources: {run.source_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
