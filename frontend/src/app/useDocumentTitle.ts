import { useEffect } from "react";

/**
 * Set `document.title = "${title} · BidDesk"` for the mounted page.
 * Each page calls this once; the AppShell doesn't need to know which
 * title belongs to which route. The previous title is NOT restored on
 * unmount — the next page sets its own, and any gap-second is
 * imperceptible.
 */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · BidDesk`;
  }, [title]);
}
