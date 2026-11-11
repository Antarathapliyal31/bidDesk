import { CheckIcon, PencilIcon, UserPlusIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Line-edit origin marker (SPEC §9.4). Full behavior arrives with the
 * bid workspace in Phase 8; this stub exists so the visual language is
 * settled now and the type is shared across surfaces.
 */
type Origin = "agent" | "user_filled" | "user_overridden";

const CONFIG: Record<Origin, { icon: React.ComponentType<{ className?: string }>; label: string }> =
  {
    agent: { icon: CheckIcon, label: "Agent" },
    user_filled: { icon: UserPlusIcon, label: "You filled" },
    user_overridden: { icon: PencilIcon, label: "You overrode" },
  };

export function OriginMarker({
  origin,
  className,
}: {
  origin: Origin;
  className?: string;
}) {
  const { icon: Icon, label } = CONFIG[origin];
  return (
    <span
      className={cn("inline-flex items-center gap-1 text-xs text-muted-foreground", className)}
      aria-label={label}
      title={label}
    >
      <Icon className="size-3.5" aria-hidden />
    </span>
  );
}
