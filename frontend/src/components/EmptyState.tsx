import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Designed empty state (SPEC §11.4). Every "nothing here" screen uses
 * this — never a blank area. Pair with an icon, one clear next action
 * and an optional footer (e.g. sample inputs on the History page).
 *
 * Border is solid: dashed borders are reserved for drop targets
 * (PdfDropzone) so the two never read as the same affordance. Pass
 * `headingLevel` to slot the title into the page's heading order —
 * 2 by default, 3 when this empty state lives inside a section with
 * its own h2.
 */
type Props = {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  footer?: React.ReactNode;
  headingLevel?: 2 | 3;
  className?: string;
};

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  footer,
  headingLevel = 2,
  className,
}: Props) {
  const Heading = headingLevel === 3 ? "h3" : "h2";
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-md border border-border bg-card px-6 py-16 text-center",
        className,
      )}
    >
      <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
        <Icon className="size-5 text-muted-foreground" aria-hidden />
      </div>
      <Heading className="text-base font-semibold text-foreground">{title}</Heading>
      {description ? (
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
      {footer ? <div className="mt-8 w-full max-w-xl">{footer}</div> : null}
    </div>
  );
}
