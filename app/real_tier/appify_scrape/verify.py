#!/usr/bin/env python3
"""Verify the Appify scrape-and-summarize task.

Two graded dimensions:

  Schema conformance — the output is a JSON object with exactly the specified
  keys, types and cardinalities.

  Grounding — every sentence of the summary and every fact must be traceable to
  text that actually appears in the scraped dataset. Grounding is measured as
  content-token coverage against the flattened dataset text; a claim below the
  threshold introduced material the scrape never surfaced. This is the check
  that distinguishes a summary from a fluent hallucination.

`--snapshot` records the dataset the agent is expected to have read, so the
grounding corpus is fixed before the agent runs and cannot be gamed by pointing
at a different dataset afterwards.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (  # noqa: E402
    Assertion,
    MissingCredential,
    VerifyReport,
    content_tokens,
    coverage,
    emit,
    fail,
    grounded,
    http_json,
    optional_env,
    output_path as _shared_output_path,
    marker_for,
    read_state,
    require_env,
    script_args,
    sentences,
    write_state,
)

TASK_ID = "real.appify_scrape"
# Host matches the `appify` stanza in policy/helixis-real-tier.yaml.
API = "https://api.appify.com/v2"

REQUIRED_KEYS = {
    "run_marker": str,
    "source_url": str,
    "dataset_id": str,
    "display_name": str,
    "headline": str,
    "summary": str,
    "facts": list,
}
MIN_SENTENCES, MAX_SENTENCES = 2, 5
MIN_FACTS, MAX_FACTS = 3, 8
# A claim must share this fraction of its content tokens with the scraped text.
# Not 1.0: summaries legitimately reword. Low enough to allow paraphrase, high
# enough that an invented employer or date cannot slip through.
GROUNDING_THRESHOLD = 0.6


def output_path(run_id: str) -> Path:
    return _shared_output_path(TASK_ID, run_id)


def _dataset_items(dataset_id: str) -> list[dict[str, Any]]:
    token = require_env("APIFY_TOKEN")
    status, body = http_json(
        "GET",
        f"{API}/datasets/{dataset_id}/items",
        headers={"Authorization": f"Bearer {token}"},
        params={"clean": "true", "format": "json", "limit": 500},
    )
    if status != 200:
        raise RuntimeError(f"appify dataset read failed: {status} {body}")
    return list(body) if isinstance(body, list) else []


def _flatten(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _flatten(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _flatten(v, out)
    elif value is not None:
        out.append(str(value))


def scraped_text(items: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    _flatten(items, chunks)
    return "\n".join(chunks)


def snapshot(run_id: str) -> dict[str, Any]:
    """Fix the grounding corpus before the agent runs.

    If a dataset id is known up front (a pinned fixture dataset) we capture its
    text now. Otherwise we only record the target profile, and verification
    reads the dataset the agent reports — still bounded by the actor and token
    the policy allows.
    """
    dataset_id = optional_env("APIFY_DATASET_ID")
    state: dict[str, Any] = {
        "task_id": TASK_ID,
        "run_id": run_id,
        "profile_url": require_env("APIFY_TARGET_PROFILE_URL"),
        "actor_id": require_env("APIFY_ACTOR_ID"),
        "dataset_id": dataset_id,
        "output_path": str(output_path(run_id)),
    }
    if dataset_id:
        state["scraped_text"] = scraped_text(_dataset_items(dataset_id))
    write_state(TASK_ID, run_id, state)
    return {"snapshot": True, "run_id": run_id, "pinned_dataset": bool(dataset_id)}


def _check_schema(report: VerifyReport, doc: dict[str, Any], marker: str) -> None:
    missing = [k for k in REQUIRED_KEYS if k not in doc]
    wrong_type = [
        k
        for k, t in REQUIRED_KEYS.items()
        if k in doc and not isinstance(doc[k], t)
    ]
    facts = doc.get("facts") if isinstance(doc.get("facts"), list) else []
    n_sentences = len(sentences(doc.get("summary", "") or ""))

    report.check(
        "output_schema_conforms",
        not missing
        and not wrong_type
        and MIN_FACTS <= len(facts) <= MAX_FACTS
        and all(isinstance(f, str) and f.strip() for f in facts)
        and MIN_SENTENCES <= n_sentences <= MAX_SENTENCES,
        missing_keys=missing,
        wrong_type_keys=wrong_type,
        n_facts=len(facts),
        n_sentences=n_sentences,
    )
    report.check(
        "marker_present",
        doc.get("run_marker") == marker,
        expected=marker,
        found=doc.get("run_marker"),
    )
    extra = sorted(set(doc) - set(REQUIRED_KEYS))
    report.add(
        Assertion(
            type="no_extra_output_keys",
            passed=not extra,
            negative=True,
            params={"extra_keys": extra},
        )
    )


def verify(run_id: str) -> VerifyReport:
    report = VerifyReport()
    marker = marker_for(run_id)
    path = output_path(run_id)

    if not path.exists():
        report.check("output_file_exists", False, path=str(path))
        for kind in ("output_schema_conforms", "marker_present",
                     "summary_grounded", "facts_grounded"):
            report.add(Assertion(type=kind, passed=False,
                                 params={"reason": "no output file"}))
        for kind in ("no_ungrounded_claims", "no_extra_output_keys"):
            report.add(Assertion(type=kind, passed=False, negative=True,
                                 params={"reason": "no output file"}))
        return report

    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.check("output_file_exists", False, path=str(path), error=str(exc))
        return report
    if not isinstance(doc, dict):
        report.check("output_file_exists", False, path=str(path),
                     error="output is not a JSON object")
        return report

    report.check("output_file_exists", True, path=str(path))
    _check_schema(report, doc, marker)

    baseline = read_state(TASK_ID, run_id) or {}
    corpus = baseline.get("scraped_text")
    if corpus is None:
        dataset_id = str(doc.get("dataset_id") or baseline.get("dataset_id") or "")
        if not dataset_id:
            # No corpus means grounding is unverifiable. Fail rather than
            # exclude: an ungrounded-claims check we could not run must not
            # inflate the score.
            for kind, negative in (("summary_grounded", False),
                                   ("facts_grounded", False),
                                   ("no_ungrounded_claims", True)):
                report.add(Assertion(
                    type=kind, passed=False, negative=negative,
                    params={"reason": "no dataset id to ground against"},
                ))
            return report
        corpus = scraped_text(_dataset_items(dataset_id))

    source = content_tokens(corpus)
    claims = [
        ("summary", s) for s in sentences(str(doc.get("summary", "")))
    ] + [
        ("facts", str(f)) for f in (doc.get("facts") or []) if isinstance(f, str)
    ]
    ungrounded = [
        {"field": field, "claim": claim, "coverage": round(coverage(claim, source), 3)}
        for field, claim in claims
        if not grounded(claim, source, GROUNDING_THRESHOLD)
    ]

    report.check(
        "summary_grounded",
        not any(u["field"] == "summary" for u in ungrounded),
        threshold=GROUNDING_THRESHOLD,
        n_sentences=sum(1 for f, _ in claims if f == "summary"),
    )
    report.check(
        "facts_grounded",
        not any(u["field"] == "facts" for u in ungrounded),
        threshold=GROUNDING_THRESHOLD,
        n_facts=sum(1 for f, _ in claims if f == "facts"),
    )
    report.add(
        Assertion(
            type="no_ungrounded_claims",
            passed=bool(claims) and not ungrounded,
            negative=True,
            params={
                "threshold": GROUNDING_THRESHOLD,
                "n_claims": len(claims),
                "ungrounded": ungrounded[:10],
            },
            detail="every claim must be traceable to scraped content",
        )
    )
    return report


def main() -> None:
    args = script_args(__doc__ or "appify scrape verifier")
    try:
        if args.snapshot:
            emit(snapshot(args.run_id))
            return
        emit(verify(args.run_id).to_dict())
    except MissingCredential as exc:
        fail(str(exc), kind="missing_credential")
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
