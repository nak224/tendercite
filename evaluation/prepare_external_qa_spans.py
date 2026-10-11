"""Offline review-template and exact page-text export CLI; no evidence is auto-labelled."""

import argparse
import logging
from pathlib import Path

from evaluation.external_benchmark import confined
from evaluation.external_qa_spans import (
    export_pages,
    load_review_context,
    make_review_template,
    write_new_json,
)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--acquisition-dir", type=Path, required=True)
    cli.add_argument("--retrieval-results", type=Path, required=True)
    cli.add_argument("--template", type=Path, help="Fresh local JSON file")
    cli.add_argument("--pages-dir", type=Path, help="Fresh local directory for exact text pages")
    args = cli.parse_args(argv)
    if not (args.template or args.pages_dir):
        cli.error("Provide --template and/or --pages-dir")
    try:
        for path in (args.template, args.pages_dir):
            if path and confined(path).exists():
                raise ValueError("Preserve existing review files: use fresh output paths")
        if (
            args.template
            and args.pages_dir
            and args.template.resolve().is_relative_to(args.pages_dir.resolve())
        ):
            raise ValueError("Template must be separate from the page-export directory")
        context = load_review_context(args.acquisition_dir, args.retrieval_results)
        if args.pages_dir:
            index = export_pages(context, args.pages_dir)
            print(f"Exported {sum(len(d['pages']) for d in index['documents'])} exact pages")
        if args.template:
            write_new_json(args.template, make_review_template(context))
        print(
            f"{len(context.questions)} questions; {len(context.exclusions)} excluded; "
            "no reviewed spans generated"
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logging.error("Review preparation failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
