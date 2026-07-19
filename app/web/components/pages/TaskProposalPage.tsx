"use client";

import { PageShell } from "../PageShell";
import { TaskProposalReview } from "../TaskProposalReview";

/**
 * One mined task, in full, before it becomes curriculum.
 *
 * The whole spec-05 loop narrows to this page: Helixis watched real work,
 * noticed a repeat, and drafted a task from it — and none of that means
 * anything until a person looks at the draft and says yes. The intent line says
 * so out loud, because "the model generated its own curriculum" is only a good
 * story if the approval in the middle is real.
 */
export function TaskProposalPage({ id }: { id: string }) {
  return (
    <PageShell
      title="Proposed task"
      intent="A workflow Helixis noticed you repeat, drafted into a trainable task. Nothing here is in your curriculum yet — the miner proposes, you decide, and even an approved task cannot run until you have reviewed the verifier a model wrote for it."
    >
      <TaskProposalReview id={id} />
    </PageShell>
  );
}
