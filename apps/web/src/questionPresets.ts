import type { PrimaryTicker } from "./api/types";

/** Browser guidance is independent of evaluation-case qualification metadata. */
export interface GuidedQuestionPreset {
  readonly presetId: string;
  readonly ticker: PrimaryTicker;
  readonly date: string;
  readonly label: string;
  readonly question: string;
  readonly capability:
    | "Move + catalyst evidence"
    | "Historical analogues"
    | "Relationship paths"
    | "Volatility risk"
    | "Narrative topic map"
    | "Peer comparison";
}

// Each guided question demonstrates a distinct bounded research workflow. The
// ticker and cutoff travel with the question so the browser can offer one
// global showcase menu without implying that every workflow fits every date.
export const QUESTION_PRESETS: readonly GuidedQuestionPreset[] = Object.freeze([
  {
    presetId: "nvda-market-dislocation-2025-01-27",
    ticker: "NVDA",
    date: "2025-01-27",
    label: "Market dislocation",
    question: "Why did NVIDIA move on January 27, 2025? Quantify the dislocation, test whether it was an unusual shock, identify what cutoff-qualified sources support, and preserve any unresolved cause.",
    capability: "Move + catalyst evidence",
  },
  {
    presetId: "nvda-historical-analogues-2025-01-27",
    ticker: "NVDA",
    date: "2025-01-27",
    label: "Historical analogues",
    question: "Which historical analogues are most similar to NVIDIA's January 27, 2025 market shock, what measured features make them similar, and where do the comparisons break down?",
    capability: "Historical analogues",
  },
  {
    presetId: "schw-shock-propagation-2023-03-13",
    ticker: "SCHW",
    date: "2023-03-13",
    label: "Shock propagation",
    question: "Trace the bounded relationship paths through which Schwab's March 13, 2023 shock could have propagated, and explain why those paths do not prove causation.",
    capability: "Relationship paths",
  },
  {
    presetId: "nvda-volatility-risk-2025-01-27",
    ticker: "NVDA",
    date: "2025-01-27",
    label: "Volatility risk",
    question: "What did the qualified volatility-risk model estimate for NVIDIA after the January 27, 2025 shock, and what are the estimate's horizon and limitations?",
    capability: "Volatility risk",
  },
  {
    presetId: "jpm-narrative-map-2025-01-15",
    ticker: "JPM",
    date: "2025-01-15",
    label: "Narrative map",
    question: "Map the main topic clusters in JPMorgan evidence available by the January 15, 2025 close, including conflicting themes and source gaps.",
    capability: "Narrative topic map",
  },
  {
    presetId: "jpm-peer-comparison-2025-01-15",
    ticker: "JPM",
    date: "2025-01-15",
    label: "Peer comparison",
    question: "Compare JPM, GS, and SCHW at the January 15, 2025 close. Which move was most abnormal after benchmark context, and what evidence supports a shared versus company-specific explanation?",
    capability: "Peer comparison",
  },
]);

type PresetStatus = {
  readonly supported_tickers: readonly PrimaryTicker[];
  readonly coverage: { readonly first_session: string; readonly last_session: string };
};

export function availableQuestionPresets(
  status: PresetStatus,
  catalog: readonly GuidedQuestionPreset[] = QUESTION_PRESETS,
): readonly GuidedQuestionPreset[] {
  const supported = new Set(status.supported_tickers);
  return catalog.filter((preset) =>
    supported.has(preset.ticker)
    && preset.date >= status.coverage.first_session
    && preset.date <= status.coverage.last_session,
  );
}

export function findQuestionPreset(
  presetId: string,
  status: PresetStatus,
  catalog: readonly GuidedQuestionPreset[] = QUESTION_PRESETS,
): GuidedQuestionPreset | undefined {
  return availableQuestionPresets(status, catalog).find((preset) => preset.presetId === presetId);
}
