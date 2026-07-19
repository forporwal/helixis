#!/usr/bin/env python3
"""Purge the digest messages this run posted. Idempotent.

Scoping, and why:

  * Deletion candidates are only messages whose content contains
    `[helixis-run:<run_id>]`. Human chatter in the same channel cannot carry a
    marker for a run that had not started yet, so it is unreachable here.
  * The pre-run snapshot is a second, independent guard: any message id that
    existed before the run is skipped outright. Both conditions must hold.
  * Deletion goes through the WEBHOOK token
    (DELETE /webhooks/{id}/{token}/messages/{message_id}), which Discord only
    permits for messages that webhook itself authored. So even a marker
    collision on a human-authored message would be refused by the API rather
    than by our code — defence in depth, not a single check.
  * 404 / 10008 ("Unknown Message") means already deleted and counts as
    success, so re-running is a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (  # noqa: E402
    MissingCredential,
    emit,
    fail,
    http_json,
    read_state,
    require_env,
    script_args,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify import TASK_ID, marked_messages  # noqa: E402


def _webhook_parts() -> tuple[str, str]:
    """Split DISCORD_WEBHOOK_URL into (id, token) without logging either."""
    url = require_env("DISCORD_WEBHOOK_URL")
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) < 2:
        raise MissingCredential(
            "DISCORD_WEBHOOK_URL is malformed; expected"
            " https://discord.com/api/webhooks/<id>/<token>"
        )
    return parts[-2], parts[-1]


def reset(run_id: str) -> dict[str, Any]:
    webhook_id, webhook_token = _webhook_parts()
    baseline = read_state(TASK_ID, run_id) or {}
    preexisting = set(baseline.get("message_ids", []))

    deleted: list[str] = []
    already_gone: list[str] = []
    skipped_preexisting: list[str] = []

    for message in marked_messages(run_id):
        message_id = message["id"]
        if message_id in preexisting:
            skipped_preexisting.append(message_id)
            continue
        status, body = http_json(
            "DELETE",
            f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}"
            f"/messages/{message_id}",
        )
        if status in (200, 204):
            deleted.append(message_id)
        elif status == 404:
            already_gone.append(message_id)  # idempotent re-run
        else:
            raise RuntimeError(f"discord delete failed: {status} {body}")

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
    args = script_args(__doc__ or "webhook digest reset")
    try:
        emit(reset(args.run_id))
    except MissingCredential as exc:
        fail(str(exc), kind="missing_credential")
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
