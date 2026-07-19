import { auth } from "@/auth";
import { Sidebar } from "@/components/Sidebar";
import { clawTuiUrl, clawUiUrl } from "@/lib/claw";

/**
 * Authenticated shell: navigation rail + content, no header.
 *
 * The global header was removed — it held only a search box and two controls,
 * all of which now sit at the foot of the rail, and it cost every page 64px of
 * vertical space above the fold. The session is read here (server side) and
 * handed to the rail, which is a client component and cannot call auth().
 *
 * The Claw URLs are read here for the same reason: the Control UI href embeds
 * the gateway token from the environment, which only the server can see.
 */
export default async function AppLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const session = await auth();

  return (
    <>
      <Sidebar
        email={session?.user?.email}
        clawUiUrl={clawUiUrl()}
        clawTuiUrl={clawTuiUrl()}
      />
      <div className="flex min-w-0 flex-1 flex-col">{children}</div>
    </>
  );
}
