#!/usr/bin/env python3
"""Delete the drafts this run created. Idempotent.

Scoping, and why it is written this way:

  * The ONLY drafts considered for deletion are those whose subject or body
    contains `[helixis-run:<run_id>]`. A draft the account owner wrote by hand
    cannot contain a marker for a run that had not started when they wrote it,
    so pre-existing user data is structurally unreachable from here.
  * The pre-run snapshot is used as a second, independent guard: any draft id
    that already existed before the run is skipped even if it somehow matched
    the marker. Two independent conditions must both hold before a delete.
  * A draft that is already gone returns 404 from the API; that is treated as
    success, so a second `reset.py` run deletes nothing and exits 0.

This script never touches the INBOX, SENT, or trash.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (  # noqa: E402
    MissingCredential,
    emit,
    fail,
    gmail_service,
    is_not_found,
    read_state,
    script_args,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify import TASK_ID, _marked_drafts  # noqa: E402


def reset(run_id: str) -> dict[str, Any]:
    service = gmail_service()
    baseline = read_state(TASK_ID, run_id) or {}
    preexisting = set(baseline.get("draft_ids", []))

    candidates = _marked_drafts(service, run_id)
    deleted: list[str] = []
    already_gone: list[str] = []
    skipped_preexisting: list[str] = []

    for draft in candidates:
        draft_id = draft["draft_id"]
        if draft_id in preexisting:
            # Second guard: existed before the run, so this run did not create
            # it. Never delete it, marker or not.
            skipped_preexisting.append(draft_id)
            continue
        try:
            service.users().drafts().delete(userId="me", id=draft_id).execute()
            deleted.append(draft_id)
        except Exception as exc:
            if is_not_found(exc):
                already_gone.append(draft_id)  # idempotent re-run
                continue
            raise

    return {
        "task_id": TASK_ID,
        "run_id": run_id,
        "deleted": deleted,
        "already_gone": already_gone,
        "skipped_preexisting": skipped_preexisting,
        "had_baseline": bool(baseline),
        "ok": True,
    }


def main() -> None:
    args = script_args(__doc__ or "gmail triage reset")
    try:
        emit(reset(args.run_id))
    except MissingCredential as exc:
        fail(str(exc), kind="missing_credential")
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
