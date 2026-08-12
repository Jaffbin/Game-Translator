from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agl.models import EntryStatus
from agl.project import ProjectStore
from agl.qa import (
    load_glossary,
    run_qa_entries,
    summarize_issues,
)


def cmd_check(args: argparse.Namespace) -> None:
    with ProjectStore(args.project_dir) as store:
        glossary_path = Path(store.project_dir) / "glossary.csv"
        glossary = load_glossary(glossary_path)

        entries = store.all_entries(status=args.status)

        target_language = store.meta.get("target_language", "")

        results, stats = run_qa_entries(
            entries=entries,
            target_language=target_language,
            glossary=glossary,
        )

        entries_by_id = {entry.id: entry for entry in entries}

        # --------------------------------------------------------------
        # Optional JSON report
        # --------------------------------------------------------------

        if args.output:
            report = {
                "project": store.meta,
                "summary": stats,
                "results": [],
            }

            for result in results:
                if result.passed and not result.warnings and not args.include_passed:
                    continue

                entry = entries_by_id.get(result.entry_id)

                report["results"].append(
                    {
                        "entry_id": result.entry_id,
                        "file_path": entry.file_path if entry else "",
                        "context": entry.context if entry else "",
                        "status": entry.status.value if entry else "",
                        "source_text": entry.source_text if entry else "",
                        "target_text": entry.target_text if entry else "",
                        "passed": result.passed,
                        "errors": [
                            {
                                "code": issue.code,
                                "message": issue.message,
                            }
                            for issue in result.errors
                        ],
                        "warnings": [
                            {
                                "code": issue.code,
                                "message": issue.message,
                            }
                            for issue in result.warnings
                        ],
                    }
                )

            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            output_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            print(f"QA report written to: {output_path}")

        # --------------------------------------------------------------
        # Optional apply to project
        # --------------------------------------------------------------

        if args.apply:
            updated = 0

            for result in results:
                entry = entries_by_id.get(result.entry_id)

                if entry is None:
                    continue

                if entry.locked or entry.ignored:
                    continue

                note = summarize_issues(result)

                if result.errors:
                    store.update_entry(
                        entry.id,
                        status=EntryStatus.ERROR,
                        note=note,
                    )
                    updated += 1

                elif result.warnings and args.apply_warnings:
                    if entry.status not in (
                        EntryStatus.REVIEWED,
                        EntryStatus.LOCKED,
                    ):
                        store.update_entry(
                            entry.id,
                            status=EntryStatus.NEEDS_REVIEW,
                            note=note,
                        )
                        updated += 1

            store.save_meta()

            print(f"Updated {updated} project entries from QA results.")

        # --------------------------------------------------------------
        # Console summary
        # --------------------------------------------------------------

        print("-" * 70)
        print("QA summary")
        print(f"  Total entries:        {stats['total_entries']}")
        print(f"  Passed entries:       {stats['passed_entries']}")
        print(f"  Failed entries:       {stats['failed_entries']}")
        print(f"  Entries with errors:  {stats['entries_with_errors']}")
        print(f"  Entries with warns:   {stats['entries_with_warnings']}")

        issue_counts = stats.get("issue_counts", {})

        if issue_counts:
            print()
            print("Issue counts:")

            for code, count in sorted(issue_counts.items()):
                print(f"  {code}: {count}")

        if args.fail_on_error and stats["entries_with_errors"] > 0:
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 6: translation quality assurance"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_check = subparsers.add_parser(
        "check",
        help="Run QA checks on project entries",
    )

    p_check.add_argument("project_dir")

    p_check.add_argument(
        "--status",
        default=None,
        choices=[status.value for status in EntryStatus],
        help="Only check entries with this status",
    )

    p_check.add_argument(
        "--output",
        default=None,
        help="Write JSON QA report to this path",
    )

    p_check.add_argument(
        "--include-passed",
        action="store_true",
        help="Include fully passed entries in JSON report",
    )

    p_check.add_argument(
        "--apply",
        action="store_true",
        help="Mark entries with QA errors as error status",
    )

    p_check.add_argument(
        "--apply-warnings",
        action="store_true",
        help="Also mark warning-only entries as needs_review",
    )

    p_check.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit with non-zero code if QA errors exist",
    )

    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()