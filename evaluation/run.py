"""Run against a real API: python -m evaluation.run --analysis --output /tmp/eval.json."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from evaluation.generate import make_pdf
from evaluation.metrics import extraction_metrics, retrieval_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument(
        "--analysis", action="store_true", help="Send synthetic text to configured LLM"
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    gold = json.loads(Path(__file__).with_name("gold.json").read_text())
    with httpx.Client(base_url=args.api, timeout=180) as client:
        response = client.post(
            "/api/v1/documents",
            files={
                "file": (
                    "synthetic-tender.pdf",
                    make_pdf([c["quote"] for c in gold["cases"]]),
                    "application/pdf",
                )
            },
        )
        response.raise_for_status()
        document = response.json()
        hits = {}
        for case in gold["cases"]:
            result = client.post(
                "/api/v1/search",
                json={
                    "query": case["query"],
                    "document_ids": [document["id"]],
                    "top_k": args.top_k,
                },
            )
            result.raise_for_status()
            hits[case["id"]] = result.json()
        report = {
            "dataset_version": gold["dataset_version"],
            "created_at": datetime.now(UTC).isoformat(),
            "mode": "live-api",
            "configuration": client.get("/api/v1/configuration").json(),
            "document_id": document["id"],
            "retrieval": retrieval_metrics(gold["cases"], hits, args.top_k),
            "retrieval_hits": hits,
            "extraction": None,
            "analysis_run": None,
        }
        if args.analysis:
            response = client.post(
                "/api/v1/analyses", json={"document_ids": [document["id"]], "top_k": 12}
            )
            response.raise_for_status()
            report["analysis_run"] = response.json()
            report["extraction"] = extraction_metrics(
                gold["cases"], response.json()["findings"], document["id"]
            )
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote live evaluation report to {args.output}")


if __name__ == "__main__":
    main()
