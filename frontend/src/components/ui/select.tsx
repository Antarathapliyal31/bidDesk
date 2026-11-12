import * as React from "react"
import { cn } from "cn"
import { CheckIcon, ChevronDownIcon, ChevronUpIcon } from "lucide-react"
import { Select as SelectPrimitive } from "radix-ui"

function Select({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Root>) {
  return <SelectPrimitive.Root data-slot="select" {...props} />
}

function SelectGroup({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Group>) {
  return <SelectPrimitive.Group data-slot="select-group" {...props} />
}

function SelectValue({
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Value>) {
  return <SelectPrimitive.Value data-slot="select-value" {...props} />
}

function SelectTrigger({
  className,
  size = "default",
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Trigger> & {
  size?: "sm" | "default"
}) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      data-size={size}
      className={cn(
        "flex w-fit items-center justify-between gap-2 rounded-md border border-input bg-transparent px-3 py-2 text-sm whitespace-nowrap shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 data-[placeholder]:text-muted-foreground data-[size=default]:h-9 data-[size=sm]:h-8 *:data-[slot=select-value]:line-clamp-1 *:data-[slot=select-value]:flex *:data-[slot=select-value]:items-center *:data-[slot=select-value]:gap-2 dark:bg-input/30 dark:hover:bg-input/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&_svg:not([class*='text-'])]:text-muted-foreground",
        className
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDownIcon className="size-4 opacity-50" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
}

/**
 * If the body has been locked with `pointer-events: none` AND no
 * other Radix modal (Dialog / AlertDialog) is open, release the
 * lock. If a Dialog IS open, leave the lock alone — it owns the
 * lock and will release it on its own close.
 *
 * Detection: Radix Dialog mounts its content with `role="dialog"`
 * (AlertDialog with `role="alertdialog"`). While open, the content
 * is in the DOM; on close it unmounts. A `querySelector` for
 * either role answers "is any Radix modal visible right now?".
 */
function maybeReleaseBodyPointerLock(): void {
  if (typeof document === "undefined") return;
  if (document.body.style.pointerEvents !== "none") return;
  const openModal = document.querySelector('[role="dialog"], [role="alertdialog"]');
  if (openModal !== null) return;
  document.body.style.pointerEvents = "";
}

function SelectContent({
  className,
  children,
  position = "item-aligned",
  align = "center",
  onPointerDown,
  onKeyDown,
  onCloseAutoFocus,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Content>) {
  // Track whether the Select is closing because of a pointer
  // interaction (click on an item) vs a keyboard interaction
  // (Enter/Space on an item, or Escape). Pointer-initiated closes
  // skip the auto-focus-return to the trigger — otherwise the
  // first outside click (e.g. Run) is absorbed by Radix's brief
  // body pointer-events lock (radix-ui #1859). Keyboard closes
  // keep the default focus-return so Tab order stays sane.
  const closedByPointerRef = React.useRef(false);

  // Belt-and-suspenders fix for the same issue (radix-ui #1859):
  // when the Select content unmounts after a close, make sure the
  // body isn't left with `pointer-events: none`. In jsdom this
  // never triggers (Radix doesn't set the lock), but in real
  // Chrome / Safari the user's next click would land during the
  // brief window and get swallowed. The `onCloseAutoFocus`
  // override alone isn't enough — Radix sets the body style
  // during scroll-lock, not during focus restore — so we clean
  // up here regardless of the close path.
  //
  // Guard: only reset if no other Radix modal (Dialog / AlertDialog)
  // is currently open. If a Dialog is open, it legitimately needs
  // the body pointer-events lock — stealing it here would unlock
  // outside interaction while the Dialog still demands focus
  // containment. Radix Dialog will release the lock itself when it
  // closes.
  React.useEffect(() => {
    return () => {
      maybeReleaseBodyPointerLock();
    };
  }, []);
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        data-slot="select-content"
        className={cn(
          "relative z-50 max-h-(--radix-select-content-available-height) min-w-[8rem] origin-(--radix-select-content-transform-origin) overflow-x-hidden overflow-y-auto rounded-md border bg-popover text-popover-foreground shadow-md data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          position === "popper" &&
            "data-[side=bottom]:translate-y-1 data-[side=left]:-translate-x-1 data-[side=right]:translate-x-1 data-[side=top]:-translate-y-1",
          className
        )}
        position={position}
        align={align}
        onPointerDown={(e) => {
          closedByPointerRef.current = true;
          onPointerDown?.(e);
        }}
        onKeyDown={(e) => {
          closedByPointerRef.current = false;
          onKeyDown?.(e);
        }}
        onCloseAutoFocus={(e) => {
          // Pointer close: skip focus-return so the next click
          // outside isn't absorbed by the brief
          // `pointer-events: none` window.
          // Keyboard close: let Radix's default run so focus
          // returns to the trigger (standard accessibility behavior).
          if (closedByPointerRef.current) e.preventDefault();
          closedByPointerRef.current = false;
          // Nudge body pointer-events back now (the useEffect
          // cleanup runs AFTER unmount on the next tick; this
          // closes the window where Chrome's compositor picks
          // the "none" value for a pending pointerdown). Guarded
          // the same way as the unmount cleanup — see below.
          maybeReleaseBodyPointerLock();
          onCloseAutoFocus?.(e);
        }}
        {...props}
      >
        <SelectScrollUpButton />
        <SelectPrimitive.Viewport
          className={cn(
            "p-1",
            position === "popper" &&
              "h-[var(--radix-select-trigger-height)] w-full min-w-[var(--radix-select-trigger-width)] scroll-my-1"
          )}
        >
          {children}
        </SelectPrimitive.Viewport>
        <SelectScrollDownButton />
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  )
}

function SelectLabel({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Label>) {
  return (
    <SelectPrimitive.Label
      data-slot="select-label"
      className={cn("px-2 py-1.5 text-xs text-muted-foreground", className)}
      {...props}
    />
  )
}

function SelectItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      className={cn(
        "relative flex w-full cursor-default items-center gap-2 rounded-sm py-1.5 pr-8 pl-2 text-sm outline-hidden select-none focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&_svg:not([class*='text-'])]:text-muted-foreground *:[span]:last:flex *:[span]:last:items-center *:[span]:last:gap-2",
        className
      )}
      {...props}
    >
      <span
        data-slot="select-item-indicator"
        className="absolute right-2 flex size-3.5 items-center justify-center"
      >
        <SelectPrimitive.ItemIndicator>
          <CheckIcon className="size-4" />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  )
}

function SelectSeparator({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Separator>) {
  return (
    <SelectPrimitive.Separator
      data-slot="select-separator"
      className={cn("pointer-events-none -mx-1 my-1 h-px bg-border", className)}
      {...props}
    />
  )
}

function SelectScrollUpButton({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.ScrollUpButton>) {
  return (
    <SelectPrimitive.ScrollUpButton
      data-slot="select-scroll-up-button"
      className={cn(
        "flex cursor-default items-center justify-center py-1",
        className
      )}
      {...props}
    >
      <ChevronUpIcon className="size-4" />
    </SelectPrimitive.ScrollUpButton>
  )
}

function SelectScrollDownButton({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.ScrollDownButton>) {
  return (
    <SelectPrimitive.ScrollDownButton
      data-slot="select-scroll-down-button"
      className={cn(
        "flex cursor-default items-center justify-center py-1",
        className
      )}
      {...props}
    >
      <ChevronDownIcon className="size-4" />
    </SelectPrimitive.ScrollDownButton>
  )
}

export {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectScrollDownButton,
  SelectScrollUpButton,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
}
