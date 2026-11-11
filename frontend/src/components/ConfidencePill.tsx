import { cn } from "@/lib/utils";

/**
 * Pricing confidence chip (SPEC §8.3). Full behavior lands with the
 * bid workspace in Phase 8; this stub renders the visual shape so
 * design reviews can see it today. Pairs color with text, never color
 * alone. WCAG AA on card/background via the *-text tokens.
 */
type Props = {
  confidence: "high" | "medium" | "low";
  className?: string;
};

const CLASSES: Record<Props["confidence"], string> = {
  high: "border-transparent text-done-green-text",
  medium: "border-transparent text-warn-amber-text",
  low: "border-transparent text-fail-red-text",
};
const LABEL: Record<Props["confidence"], string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

export function ConfidencePill({ confidence, className }: Props) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-1.5 py-0.5 text-xs font-medium",
        CLASSES[confidence],
        className,
      )}
    >
      {LABEL[confidence]}
    </span>
  );
}
