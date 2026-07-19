#!/usr/bin/env python3
"""Reset for the Appify scrape task — a genuine no-op remotely. Idempotent.

Nothing on the Appify account is mutated by this task: the agent starts an
actor run and reads its dataset, both of which are read-only with respect to
prior state. Actor runs are immutable history and are deliberately NOT deleted
here — they are the audit trail proving what the agent actually fetched.

The only cleanup is local: the run's own output file, addressed by run id, so
this can never remove another run's artifact. A file that is already gone is
success, so a second invocation is a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import MissingCredential, emit, fail, script_args  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify import TASK_ID, output_path  # noqa: E402


def reset(run_id: str) -> dict[str, Any]:
    # Path is derived from the run id, never from a glob, so no other run's
    # output is reachable from here.
    path = output_path(run_id)
    existed = path.exists()
    if existed:
        path.unlink()

    return {
        "task_id": TASK_ID,
        "run_id": run_id,
        "remote_actions": [],
        "note": "no remote state mutated; actor run history intentionally kept",
        "local_output_removed": existed,
        "already_clean": not existed,
        "ok": True,
    }


def main() -> None:
    args = script_args(__doc__ or "appify scrape reset")
    try:
        emit(reset(args.run_id))
    except MissingCredential as exc:
        fail(str(exc), kind="missing_credential")
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
