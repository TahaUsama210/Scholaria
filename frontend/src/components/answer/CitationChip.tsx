import { sourceLabel } from "@/lib/citations";
import { cn } from "@/lib/utils";
import type { Citation } from "@/types/api";

interface Props {
  marker: number;
  source?: Citation;
  active?: boolean;
  onSelect?(marker: number): void;
}

/** Inline `[n]` marker rendered as a button that reveals the matching source card. */
export function CitationChip({ marker, source, active, onSelect }: Props) {
  const label = sourceLabel(marker, source);
  return (
    <button
      type="button"
      onClick={() => onSelect?.(marker)}
      aria-label={`Show ${label.charAt(0).toLowerCase()}${label.slice(1)}`}
      title={label}
      data-marker={marker}
      data-active={active ? "true" : undefined}
      className={cn(
        "mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] -translate-y-px items-center justify-center",
        "rounded-md px-1 align-middle text-[0.7rem] font-semibold leading-none tabular-nums",
        "transition-colors focus-visible:ring-offset-1",
        active
          ? "bg-accent text-accent-fg"
          : "bg-accent/10 text-accent-text hover:bg-accent/20",
      )}
    >
      {marker}
    </button>
  );
}
