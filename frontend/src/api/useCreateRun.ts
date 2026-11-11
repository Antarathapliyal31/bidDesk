import { useMutation, useQueryClient } from "@tanstack/react-query";

import { withDelay } from "@/dev-fixtures";

import type { ApiError } from "./ApiError";
import type { FaultKey } from "./useFaults";

/**
 * Start a run. Fixture-backed on this branch (sleeps, exercises the
 * FormData builder, returns a canned `run_id`). The Phase-5 merge
 * replaces the `mutationFn` body with
 *   const res = await fetch("/api/runs", { method: "POST", body: toCreateRunFormData(payload) });
 *   if (!res.ok) throw await apiErrorFromResponse(res);
 *   return res.json();
 * — call sites, payload shape, and FormData serialization don't
 * change.
 *
 * On success we `invalidateQueries({ queryKey: ["runs"] })` so the
 * History page re-fetches without a reload.
 *
 * To force a failure in tests, mock this module with `vi.mock` and
 * return a mutationFn that rejects with an `ApiError`. See
 * tests/NewBidPage.test.tsx.
 */

/**
 * Payload union: `file` XOR `text`. `text?: never` on the file
 * variant (and vice versa) makes TypeScript reject objects that try
 * to set both.
 */
export type CreateRunPayload =
  | { file: File; text?: never; branch_id: string; faults: FaultKey[] }
  | { file?: never; text: string; branch_id: string; faults: FaultKey[] };

export type CreateRunResult = { run_id: string };

/**
 * Serialise a `CreateRunPayload` to the multipart body shape the
 * backend expects (SPEC §13 `POST /api/runs`). Exposed so both the
 * fixture mutationFn and the Phase-5 fetch share one builder.
 */
export function toCreateRunFormData(payload: CreateRunPayload): FormData {
  const fd = new FormData();
  if (payload.file !== undefined) {
    fd.set("file", payload.file, payload.file.name);
  } else {
    fd.set("text", payload.text);
  }
  fd.set("branch_id", payload.branch_id);
  // Faults as a JSON string — matches `runs.faults_json` on the
  // server (SPEC §5.1) and keeps multipart parsing simple. Omit the
  // field entirely when the list is empty: SPEC §10.6 + API_CONTRACT
  // say "faults absent or [] is always accepted; a non-empty list
  // requires demo_faults_enabled" — the server may hard-reject a
  // non-empty value with the flag off, so sending "[]" would risk
  // a spurious rejection.
  if (payload.faults.length > 0) {
    fd.set("faults", JSON.stringify(payload.faults));
  }
  return fd;
}

export function useCreateRun() {
  const qc = useQueryClient();
  return useMutation<CreateRunResult, ApiError, CreateRunPayload>({
    mutationFn: async (payload) => {
      // Exercise the FormData builder so the Phase-5 swap is a
      // one-line fetch change, not "also now build a FormData".
      // The result is ignored in fixture mode but kept so a typo in
      // the builder fails here, not after the swap.
      toCreateRunFormData(payload);
      return withDelay({ run_id: "run_demo_a1" }, 800);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}
