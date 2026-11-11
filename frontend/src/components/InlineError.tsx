import { AlertCircleIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Small inline error used next to form fields and dropzones. Pairs
 * red text with an icon so it's not color-alone (SPEC §11.5).
 */
type Props = {
  children: React.ReactNode;
  className?: string;
  /**
   * Optional element id so callers (SubmitBar, BranchSelect, …) can
   * wire `aria-describedby` from the control that owns the error.
   */
  id?: string;
};

export function InlineError({ children, className, id }: Props) {
  return (
    <p
      id={id}
      role="alert"
      className={cn("mt-1.5 flex items-center gap-1.5 text-xs text-fail-red-text", className)}
    >
      <AlertCircleIcon className="size-3.5 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  );
}
