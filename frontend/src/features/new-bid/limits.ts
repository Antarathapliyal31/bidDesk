/**
 * Re-export shim. The real limits live in `src/lib/limits.ts` so
 * `apiMessages` + `validation` can both import them without a
 * features-to-features dependency. Keep this file for callers that
 * reach in via `./limits`.
 */
export {
  MAX_UPLOAD_MB,
  MAX_UPLOAD_BYTES,
  MAX_PAGES,
  MAX_TEXT_CHARS,
} from "@/lib/limits";
