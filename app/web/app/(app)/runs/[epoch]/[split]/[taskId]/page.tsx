"use client";

import { useParams } from "next/navigation";
import { TrajectoryViewer } from "@/components/TrajectoryViewer";

export default function EpisodePage() {
  const params = useParams<{ epoch: string; split: string; taskId: string }>();
  return (
    <TrajectoryViewer
      epoch={Number(params.epoch)}
      split={params.split}
      taskId={decodeURIComponent(params.taskId)}
    />
  );
}
