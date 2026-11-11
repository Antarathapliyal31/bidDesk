/**
 * Platform detection for keyboard-shortcut labels. We never gate
 * behavior on IS_MAC — behavior is "⌘ or Ctrl". Only the *label*
 * shown to the user changes ("⌘K" on Mac, "Ctrl+K" elsewhere).
 */
export const IS_MAC: boolean =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.platform);

export const MOD_KEY_LABEL = IS_MAC ? "⌘" : "Ctrl";
export const MOD_KEY_LABEL_SR = IS_MAC ? "Command" : "Control";

/** True iff a keyboard event carries the "modifier" key (⌘ on Mac, Ctrl elsewhere). */
export function hasModKey(e: KeyboardEvent | React.KeyboardEvent): boolean {
  return IS_MAC ? e.metaKey : e.ctrlKey;
}
