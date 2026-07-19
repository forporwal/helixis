"use client";

import Link from "next/link";
import { Play } from "lucide-react";
import { Button } from "./ui/button";

/**
 * The one action every read-only surface should offer.
 *
 * Tasks and Wiki both answer "what happened"; neither used to answer "how do I
 * make more happen", so the only route to a run was knowing that Lab exists.
 * This deliberately navigates to Lab rather than POSTing
 * start-epoch directly: a run costs money and picks an epoch number, so the
 * confirmation step is the point, not friction to be removed.
 *
 * Containment is excluded on purpose — it is a review queue whose actions
 * (approve/reject) are per-proposal and already inline in the feed.
 */
export function StartRunAction() {
  return (
    <Button asChild size="sm">
      <Link href="/lab">
        <Play aria-hidden />
        Start a run
      </Link>
    </Button>
  );
}
