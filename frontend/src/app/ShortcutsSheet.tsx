import { Kbd } from "@/components/Kbd";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

import { MOD_KEY_LABEL, MOD_KEY_LABEL_SR } from "./platform";
import { useShortcutsSheet } from "./ShortcutsContext";

/**
 * Keyboard-shortcuts help sheet (SPEC §11.5). Opens on "?" or via
 * the sidebar footer button. A shortcut appears in this list only
 * in the same change that implements it — so the sheet is always
 * an accurate map of what the app actually does.
 *
 * Shortcuts are grouped by scope: "Global" are always available,
 * "New bid page" fires only on /new, etc. Single-key rows are dimmed
 * and annotated "(off)" when the WCAG 2.1.4 toggle is disabled;
 * mod-key combos always work.
 */

type Row = {
  label: string;
  keys: React.ReactNode;
  /** Suppressed when the single-key switch is off. */
  singleKey: boolean;
};

type Group = {
  heading: string;
  rows: Row[];
};

export function ShortcutsSheet() {
  const { open, setOpen, singleKeyEnabled, setSingleKeyEnabled } = useShortcutsSheet();

  const groups: Group[] = [
    {
      heading: "Global",
      rows: [
        {
          label: "Open command menu",
          keys: (
            <>
              <Kbd label={MOD_KEY_LABEL_SR}>{MOD_KEY_LABEL}</Kbd>
              <Kbd>K</Kbd>
            </>
          ),
          singleKey: false,
        },
        { label: "New bid", keys: <Kbd>n</Kbd>, singleKey: true },
        {
          label: "History",
          keys: (
            <>
              <Kbd>g</Kbd>
              <Kbd>h</Kbd>
            </>
          ),
          singleKey: true,
        },
        { label: "This sheet", keys: <Kbd>?</Kbd>, singleKey: true },
      ],
    },
    {
      heading: "New bid page",
      rows: [
        {
          // Page-local: only fires on /new. Mod-key combo, so it
          // isn't affected by the single-key toggle.
          label: "Run bid",
          keys: (
            <>
              <Kbd label={MOD_KEY_LABEL_SR}>{MOD_KEY_LABEL}</Kbd>
              <Kbd label="Enter">↵</Kbd>
            </>
          ),
          singleKey: false,
        },
      ],
    },
  ];

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>Shortcuts available in BidDesk.</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {groups.map((g) => (
            <section key={g.heading} className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {g.heading}
              </h3>
              <ul
                role="list"
                className="divide-y divide-border rounded-md border border-border"
              >
                {g.rows.map((r) => {
                  const suppressed = r.singleKey && !singleKeyEnabled;
                  return (
                    <li
                      key={r.label}
                      className={cn(
                        "flex items-center justify-between px-3 py-2 text-sm",
                        suppressed && "text-muted-foreground",
                      )}
                    >
                      <span>
                        {r.label}
                        {suppressed ? <span className="ml-2">(off)</span> : null}
                      </span>
                      <span className={cn("flex items-center gap-1", suppressed && "opacity-60")}>
                        {r.keys}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>

        <div className="mt-4 flex items-start gap-3 rounded-md border border-border bg-muted/50 px-3 py-2.5">
          <input
            id="single-key-switch"
            type="checkbox"
            className="mt-0.5 size-4 rounded border-border accent-primary"
            checked={singleKeyEnabled}
            onChange={(e) => setSingleKeyEnabled(e.target.checked)}
            aria-describedby="single-key-switch-desc"
          />
          <div className="flex-1">
            <label htmlFor="single-key-switch" className="cursor-pointer text-sm font-medium">
              Single-key shortcuts
            </label>
            <p id="single-key-switch-desc" className="text-sm text-muted-foreground">
              Turn off if letter keys are triggering shortcuts by accident. {MOD_KEY_LABEL}
              <span aria-hidden>+</span>K will still work.
            </p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
