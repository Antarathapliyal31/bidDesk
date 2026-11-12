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
export type {
  AgentName,
  BudgetNotice,
  ConflictSide,
  ConflictView,
  ItemKind,
  ItemView,
  PageRoute,
  PricedLineView,
  RunEvent,
  RunEventType,
  SourceRef,
  TransferOption,
} from "./events";
export type {
  Bid,
  BidDiff,
  BidKind,
  BidLine,
  BidTotals,
  Confidence,
  ContractorQuestionView,
  AssumptionView,
  ExclusionLine,
  LaborLine,
  UnreadableLine,
  EditOp,
  SetQuantityOp,
  SetUnitPriceOp,
  SetSkuOp,
  SetDescriptionOp,
  AcceptLineOp,
  DeleteLineOp,
  AddLineOp,
  ApplyRepriceOp,
  MatchInfo,
  MatchStatus,
  MatchAssumption,
  Origin,
  RepriceProposal,
  RequirementView,
  ReviewReasonCode,
  ReviewState,
  StockInfo,
} from "./bid";
export {
  isReusedStream,
  useRunStream,
  type UseRunStreamOptions,
  type UseRunStreamResult,
} from "./useRunStream";
export { useBidDraft, type UseBidDraftResult } from "./useBidDraft";
export { useBranches, type Branch } from "./useBranches";
export {
  useCreateRun,
  toCreateRunFormData,
  type CreateRunPayload,
  type CreateRunResult,
} from "./useCreateRun";
export { useRunSummary, type RunSummary as RunSummaryDetail } from "./useRunSummary";
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
