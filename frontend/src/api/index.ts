/**
 * Public surface for data hooks + their types. Features import from
 * here (not from `@/dev-fixtures`). When the Phase 5 merge replaces
 * the hook bodies with real fetch calls, call sites don't change.
 */
export {
  ApiError,
  apiErrorFromResponse,
  isApiError,
  type ApiErrorCode,
} from "./ApiError";
export { useBranches, type Branch } from "./useBranches";
export {
  useCreateRun,
  toCreateRunFormData,
  type CreateRunPayload,
  type CreateRunResult,
} from "./useCreateRun";
export { useSamples, type Sample } from "./useSamples";
export { useFaults, type Fault, type FaultKey, type FaultsMeta } from "./useFaults";
export {
  useRuns,
  type RunSummary,
  type RunStatus,
  type Outcome,
  type InputKind,
  type DiffSummary,
  type RunsPage,
  type UseRunsParams,
} from "./useRuns";
