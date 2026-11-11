import { cn } from "@/lib/utils";

/**
 * Keyboard key pill. Used in the command menu, shortcuts sheet and
 * disabled-state hints ("⌘↵" on the finalize button). On non-Mac,
 * callers render Ctrl via IS_MAC in app/platform.ts — this component
 * just renders what it's given.
 *
 * Symbol keys (⌘, ↵, ⇧, ⌥, ⌃) are meaningless to screen readers; pass
 * `label` (e.g. "Command") so the pill is announced correctly. Plain
 * letter keys don't need a label.
 */
type Props = {
  children: React.ReactNode;
  className?: string;
  label?: string;
};

export function Kbd({ children, className, label }: Props) {
  return (
    <kbd
      className={cn(
        "inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded border border-border bg-muted px-1.5 font-mono text-xs font-medium text-muted-foreground",
        className,
      )}
      {...(label !== undefined ? { "aria-label": label } : {})}
    >
      {label !== undefined ? <span aria-hidden>{children}</span> : children}
    </kbd>
  );
}
