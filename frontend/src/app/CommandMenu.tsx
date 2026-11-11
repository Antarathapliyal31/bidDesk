import { FilePlusIcon, HistoryIcon, KeyboardIcon } from "lucide-react";
import { useRef } from "react";
import { useNavigate } from "react-router-dom";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";

import { useCommandMenu } from "./CommandMenuContext";
import { useShortcutsSheet } from "./ShortcutsContext";

/**
 * ⌘K command menu (SPEC §11.5). Linear-style. Opens via ⌘K / Ctrl+K,
 * the top-right CommandMenuTrigger, or programmatically.
 *
 * Action-dispatch pattern: `onSelect` stashes the chosen action in a
 * ref and closes the dialog. Radix's DialogContent then fires
 * `onCloseAutoFocus` with an event we `preventDefault()` so the
 * default "return focus to the opener" is cancelled — then we run
 * the stashed action, which moves focus to the next page's <main>
 * (via AppShell's location effect) or into the ShortcutsSheet.
 *
 * `CommandShortcut` hints (N, G H, ?) are hidden when the WCAG
 * single-key toggle is off — those shortcuts won't fire in that
 * mode, so advertising them would be misleading.
 *
 * `keywords` on each item feed cmdk's fuzzy search so users can
 * find History by typing "runs", New bid by "rfp", etc.
 */
export function CommandMenu() {
  const { open, setOpen } = useCommandMenu();
  const { setOpen: setShortcutsOpen, singleKeyEnabled } = useShortcutsSheet();
  const navigate = useNavigate();
  const pending = useRef<null | (() => void)>(null);

  const schedule = (fn: () => void) => () => {
    // The ref is assigned when the returned onSelect handler runs
    // (user-action time), not during render — `react-hooks/refs`
    // can't see across the closure, so suppress it here.
    // eslint-disable-next-line react-hooks/refs
    pending.current = fn;
    setOpen(false);
  };

  const onCloseAutoFocus = (e: Event) => {
    const fn = pending.current;
    if (fn) {
      e.preventDefault();
      pending.current = null;
      fn();
    }
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title="Command menu"
      description="Search for a command or navigate."
      contentProps={{ onCloseAutoFocus }}
    >
      <CommandInput placeholder="Type a command or search…" />
      <CommandList>
        <CommandEmpty>No matching commands.</CommandEmpty>
        <CommandGroup heading="Navigate">
          <CommandItem
            keywords={["rfp", "upload", "quote"]}
            onSelect={schedule(() => navigate("/new"))}
          >
            <FilePlusIcon aria-hidden />
            <span>New bid</span>
            {singleKeyEnabled ? <CommandShortcut>N</CommandShortcut> : null}
          </CommandItem>
          <CommandItem
            keywords={["runs", "past", "bids"]}
            onSelect={schedule(() => navigate("/runs"))}
          >
            <HistoryIcon aria-hidden />
            <span>History</span>
            {singleKeyEnabled ? <CommandShortcut>G H</CommandShortcut> : null}
          </CommandItem>
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Help">
          <CommandItem
            keywords={["help", "keys"]}
            onSelect={schedule(() => setShortcutsOpen(true))}
          >
            <KeyboardIcon aria-hidden />
            <span>Keyboard shortcuts</span>
            {singleKeyEnabled ? <CommandShortcut>?</CommandShortcut> : null}
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
