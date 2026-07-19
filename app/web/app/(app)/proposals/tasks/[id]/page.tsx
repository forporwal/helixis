import { TaskProposalPage } from "@/components/pages/TaskProposalPage";

export const dynamic = "force-dynamic";

export const metadata = { title: "Proposed task · Helixis" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <TaskProposalPage id={id} />;
}
