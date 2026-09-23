import { describe, expect, it } from "vitest";
import {
  QUESTION_PRESETS,
  availableQuestionPresets,
  findQuestionPreset,
  type GuidedQuestionPreset,
} from "./questionPresets";

const status = {
  supported_tickers: ["NVDA", "AMD", "JPM", "GS", "SCHW"],
  coverage: { first_session: "2022-01-03", last_session: "2026-09-11" },
} as const;

const fixtures: readonly GuidedQuestionPreset[] = [
  {
    presetId: "nvda-close-2025-01-27",
    ticker: "NVDA",
    date: "2025-01-27",
    label: "Closing price",
    question: "What was NVIDIA's closing price on January 27, 2025?",
    capability: "Peer comparison",
  },
  {
    presetId: "amd-close-2027-01-04",
    ticker: "AMD",
    date: "2027-01-04",
    label: "Closing price",
    question: "What was AMD's closing price on January 4, 2027?",
    capability: "Peer comparison",
  },
];

describe("guided question presets", () => {
  it("contains exactly six distinct guided research journeys", () => {
    expect(QUESTION_PRESETS.map((preset) => ({
      presetId: preset.presetId,
      ticker: preset.ticker,
      date: preset.date,
      label: preset.label,
      question: preset.question,
      capability: preset.capability,
    }))).toEqual([
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
    expect(new Set(QUESTION_PRESETS.map((preset) => preset.presetId)).size).toBe(6);
    expect(Object.isFrozen(QUESTION_PRESETS)).toBe(true);
  });

  it("intersects the global menu with the live ticker and date contract", () => {
    expect(availableQuestionPresets(status).map((item) => item.presetId)).toEqual([
      "nvda-market-dislocation-2025-01-27",
      "nvda-historical-analogues-2025-01-27",
      "schw-shock-propagation-2023-03-13",
      "nvda-volatility-risk-2025-01-27",
      "jpm-narrative-map-2025-01-15",
      "jpm-peer-comparison-2025-01-15",
    ]);

    expect(availableQuestionPresets(status, fixtures).map((item) => item.presetId)).toEqual([
      "nvda-close-2025-01-27",
    ]);
  });

  it("looks up only an entry that remains available at runtime", () => {
    expect(findQuestionPreset("jpm-peer-comparison-2025-01-15", status)?.ticker).toBe("JPM");
    expect(findQuestionPreset("nvda-close-2025-01-27", status, fixtures)?.ticker).toBe("NVDA");
    expect(findQuestionPreset("amd-close-2027-01-04", status, fixtures)).toBeUndefined();
    expect(findQuestionPreset("", status, fixtures)).toBeUndefined();
  });
});
