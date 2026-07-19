/**
 * Helixis mark — a three-turn helix drawn as a coil seen from the side.
 *
 * Each turn is one open elliptical arc; the gap where the arc breaks is the
 * back of the coil passing behind, and the straight segments are the strand
 * travelling down between turns. That break is what makes it read as a helix
 * rather than a flat squiggle.
 *
 * Two earlier attempts are worth not repeating: mirrored strands that meet at
 * top and bottom close into lens shapes and read as an hourglass, and a plain
 * sine coil reads as a loose "S". Both were rejected on inspection at 16px.
 *
 * Drawn in `currentColor` with no background of its own, so the same glyph
 * serves the rail tile, the login card, and the browser tab.
 */
export function HelixisMark({ className = "size-5" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {/* three turns of the coil, front face */}
      <path d="M16.4 4.9A5 2.7 0 1 0 8 5.9" />
      <path d="M16.4 11.5A5 2.7 0 1 0 8 12.5" />
      <path d="M16.4 18.1A5 2.7 0 1 0 8 19.1" />
      {/* the strand descending from one turn to the next */}
      <path d="M8 5.9 16.4 11.5" />
      <path d="M8 12.5 16.4 18.1" />
    </svg>
  );
}

/**
 * Mark on its primary-filled tile — the app's avatar-scale identity, used in
 * the navigation rail and on the login card.
 */
export function HelixisLogo({
  size = "md",
  className = "",
}: {
  size?: "md" | "lg";
  className?: string;
}) {
  const tile = size === "lg" ? "size-11 rounded-xl" : "size-8 rounded-lg";
  const mark = size === "lg" ? "size-7" : "size-5";
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center bg-primary text-on-primary ${tile} ${className}`}
    >
      <HelixisMark className={mark} />
    </span>
  );
}

/** Mark plus wordmark, locked up horizontally. */
export function HelixisWordmark({ size = "md" }: { size?: "md" | "lg" }) {
  return (
    <span className="flex items-center gap-2.5">
      <HelixisLogo size={size} />
      <span
        className={`font-bold uppercase tracking-[0.18em] text-ink ${
          size === "lg" ? "text-base" : "text-sm"
        }`}
      >
        Helixis
      </span>
    </span>
  );
}
