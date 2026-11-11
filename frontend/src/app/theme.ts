/**
 * System-default dark mode. The inline script in index.html sets
 * `.dark` on <html> and `color-scheme` BEFORE React mounts
 * (prevents FOUC and tints native controls). This module keeps both
 * in sync if the user flips their OS preference while the app is open.
 *
 * Per Phase-6 scope: no toggle. Reader is the OS.
 */
function apply(isDark: boolean) {
  const el = document.documentElement;
  el.classList.toggle("dark", isDark);
  // Tints native scrollbars, file-input buttons, form autofill, etc.
  el.style.colorScheme = isDark ? "dark" : "light";
}

export function startThemeSync(): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => {};
  }
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  apply(mq.matches);
  const onChange = (e: MediaQueryListEvent) => apply(e.matches);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
