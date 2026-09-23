import { PRIMARY_TICKERS, type PrimaryTicker } from "./types";

export type DashboardOutcome = "ok" | "partial" | "no_data";
export type DashboardSourceType = "market" | "news" | "filing" | "release" | "relationship" | "model";
export type DashboardEngine = "cudf" | "cuvs" | "cugraph" | "xgboost-gpu" | "cuml" | "deterministic";
export type DashboardOperation = "get_price_context" | "detect_market_shock" | "search_news" | "market_series";

export interface DashboardCoverage {
  readonly first_session: string;
  readonly last_session: string;
  readonly session_count: number;
  readonly scenario_id: string;
  readonly vintage_status: "reconstructed_later" | "archived_at_cutoff";
}

export interface DashboardWatchlistRow {
  readonly ticker: PrimaryTicker;
  readonly outcome: DashboardOutcome;
  readonly resolved_session: string;
  readonly close: number | null;
  readonly return_1_session_pct: number | null;
  readonly return_5_sessions_pct: number | null;
  readonly opening_gap_pct: number | null;
  readonly volume_ratio: number | null;
  readonly benchmark: "QQQ" | "XLF" | "SPY";
  readonly benchmark_return_pct: number | null;
  readonly market_adjusted_return_pct: number | null;
  readonly is_shock: boolean | null;
}

export interface DashboardSeriesPoint {
  readonly session_date: string;
  readonly ticker_close: number;
  readonly benchmark_close: number;
  readonly ticker_normalized_return_pct: number;
  readonly benchmark_normalized_return_pct: number;
  readonly volume: number;
}

export interface DashboardSeries {
  readonly ticker: PrimaryTicker;
  readonly benchmark: "QQQ" | "XLF" | "SPY";
  readonly points: readonly DashboardSeriesPoint[];
}

export interface DashboardEvidence {
  readonly citation_id: string;
  readonly title: string;
  readonly url: string;
  readonly source_type: DashboardSourceType;
  readonly published_at: string;
  readonly available_at: string;
  readonly excerpt: string;
}

export interface DashboardReceipt {
  readonly operation: DashboardOperation;
  readonly ticker: PrimaryTicker | "QQQ" | "XLF" | "SPY";
  readonly engine: DashboardEngine;
  readonly device: string;
  readonly gpu_executed: boolean;
  readonly fallback_used: false;
  readonly duration_ms: number;
}

export interface DashboardLimitation {
  readonly code: string;
  readonly message: string;
  readonly affected: readonly string[];
}

export interface MarketDashboardData {
  readonly schema_version: "market-dashboard-v1";
  readonly mode: "historical_reconstruction";
  readonly requested_as_of: string;
  readonly resolved_session: string;
  readonly cutoff_at: string;
  readonly selected_ticker: PrimaryTicker;
  readonly coverage: DashboardCoverage;
  readonly watchlist: readonly DashboardWatchlistRow[];
  readonly series: DashboardSeries;
  readonly evidence: readonly DashboardEvidence[];
  readonly receipts: readonly DashboardReceipt[];
  readonly limitations: readonly DashboardLimitation[];
}

const TICKERS = PRIMARY_TICKERS as readonly string[];
const INSTRUMENTS = [...PRIMARY_TICKERS, "QQQ", "XLF", "SPY"] as const;
const OUTCOMES = ["ok", "partial", "no_data"] as const;
const ENGINES = ["cudf", "cuvs", "cugraph", "xgboost-gpu", "cuml", "deterministic"] as const;
const OPERATIONS = ["get_price_context", "detect_market_shock", "search_news", "market_series"] as const;
const SOURCE_TYPES = ["market", "news", "filing", "release", "relationship", "model"] as const;
const ROOT_KEYS = ["schema_version", "mode", "requested_as_of", "resolved_session", "cutoff_at", "selected_ticker", "coverage", "watchlist", "series", "evidence", "receipts", "limitations"] as const;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const UTC_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const UNSAFE_TEXT_RE = /(?:\bllama(?:\b|[-_/])|(?:https?|grpc):\/\/|\bbearer\s+[A-Za-z0-9._-]{6,}|\b(?:api[_ -]?key|authorization|password|secret|access[_ -]?token)\b\s*[:=])/i;

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw Error(`Invalid ${label}`);
  return value as Record<string, unknown>;
}

function exact(value: Record<string, unknown>, keys: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) throw Error(`Invalid ${label} fields`);
}

function array(value: unknown, label: string, max: number): unknown[] {
  if (!Array.isArray(value) || value.length > max) throw Error(`Invalid ${label}`);
  return value;
}

function member<T extends string>(value: unknown, options: readonly T[], label: string): T {
  if (typeof value !== "string" || !options.includes(value as T)) throw Error(`Invalid ${label}`);
  return value as T;
}

function safeText(value: unknown, label: string, max: number, min = 1): string {
  if (typeof value !== "string" || value.length < min || value.length > max || value !== value.trim() || UNSAFE_TEXT_RE.test(value)) throw Error(`Invalid or unsafe ${label}`);
  return value;
}

function finite(value: unknown, label: string, nullable = true): number | null {
  if (value === null && nullable) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) throw Error(`Invalid ${label}`);
  return value;
}

function isoDate(value: unknown, label: string): string {
  if (typeof value !== "string" || !DATE_RE.test(value)) throw Error(`Invalid ${label}`);
  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (parsed.toISOString().slice(0, 10) !== value) throw Error(`Invalid ${label}`);
  return value;
}

function utcTimestamp(value: unknown, label: string): string {
  if (typeof value !== "string" || !UTC_RE.test(value) || Number.isNaN(Date.parse(value))) throw Error(`Invalid ${label}`);
  return value;
}

function booleanOrNull(value: unknown, label: string): boolean | null {
  if (value === null || typeof value === "boolean") return value;
  throw Error(`Invalid ${label}`);
}

function publicHttps(value: unknown): string {
  if (typeof value !== "string" || value.length > 2_048) throw Error("Invalid evidence URL");
  let url: URL;
  try { url = new URL(value); } catch { throw Error("Invalid evidence URL"); }
  const hostname = url.hostname.toLowerCase();
  const privateIp = /^(?:10\.|127\.|169\.254\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/.test(hostname);
  const internal = hostname === "localhost" || hostname === "::1" || hostname === "0.0.0.0" || hostname.endsWith(".local") || hostname.endsWith(".internal") || ["agent", "model", "tools", "web"].includes(hostname);
  const credentialQuery = [...url.searchParams.keys()].some((key) => /key|token|secret|password|credential|auth/i.test(key));
  if (url.protocol !== "https:" || url.username || url.password || !hostname || privateIp || internal || credentialQuery || /llama/i.test(value)) throw Error("Unsafe evidence URL");
  return value;
}

function decodeCoverage(value: unknown): DashboardCoverage {
  const item = record(value, "dashboard coverage");
  exact(item, ["first_session", "last_session", "session_count", "scenario_id", "vintage_status"], "dashboard coverage");
  const first = isoDate(item.first_session, "coverage first session");
  const last = isoDate(item.last_session, "coverage last session");
  const count = finite(item.session_count, "coverage session count", false)!;
  if (first > last || !Number.isInteger(count) || count < 1 || count > 100_000) throw Error("Invalid dashboard coverage");
  safeText(item.scenario_id, "scenario identity", 160);
  member(item.vintage_status, ["reconstructed_later", "archived_at_cutoff"] as const, "vintage status");
  return item as unknown as DashboardCoverage;
}

function decodeWatchlist(value: unknown, coverage: DashboardCoverage): DashboardWatchlistRow[] {
  const rows = array(value, "watchlist", PRIMARY_TICKERS.length);
  if (rows.length !== PRIMARY_TICKERS.length) throw Error("Invalid watchlist length");
  return rows.map((raw, index) => {
    const item = record(raw, "watchlist row");
    exact(item, ["ticker", "outcome", "resolved_session", "close", "return_1_session_pct", "return_5_sessions_pct", "opening_gap_pct", "volume_ratio", "benchmark", "benchmark_return_pct", "market_adjusted_return_pct", "is_shock"], "watchlist row");
    const ticker = member(item.ticker, PRIMARY_TICKERS, "watchlist ticker");
    if (ticker !== PRIMARY_TICKERS[index]) throw Error("Invalid canonical watchlist order");
    const outcome = member(item.outcome, OUTCOMES, "watchlist outcome");
    const resolved = isoDate(item.resolved_session, "watchlist resolved session");
    if (resolved < coverage.first_session || resolved > coverage.last_session) throw Error("Watchlist session outside coverage");
    const close = finite(item.close, "watchlist close");
    const oneDay = finite(item.return_1_session_pct, "one-session return");
    const fiveDay = finite(item.return_5_sessions_pct, "five-session return");
    const gap = finite(item.opening_gap_pct, "opening gap");
    const volume = finite(item.volume_ratio, "volume ratio");
    const benchmarkReturn = finite(item.benchmark_return_pct, "benchmark return");
    const adjusted = finite(item.market_adjusted_return_pct, "market-adjusted return");
    const shock = booleanOrNull(item.is_shock, "shock flag");
    member(item.benchmark, ["QQQ", "XLF", "SPY"] as const, "benchmark");
    if (close !== null && close <= 0 || volume !== null && volume < 0) throw Error("Invalid watchlist metric range");
    if (outcome === "no_data" && [close, oneDay, fiveDay, gap, volume, benchmarkReturn, adjusted, shock].some((metric) => metric !== null)) throw Error("No-data row contains invented metrics");
    return item as unknown as DashboardWatchlistRow;
  });
}

function decodeSeries(value: unknown, selected: PrimaryTicker, resolved: string, watchlist: readonly DashboardWatchlistRow[]): DashboardSeries {
  const item = record(value, "dashboard series");
  exact(item, ["ticker", "benchmark", "points"], "dashboard series");
  if (item.ticker !== selected) throw Error("Series ticker does not match selection");
  const selectedRow = watchlist.find((row) => row.ticker === selected)!;
  if (item.benchmark !== selectedRow.benchmark) throw Error("Series benchmark does not match watchlist");
  const points = array(item.points, "series points", 63);
  if (points.length < 1) throw Error("Dashboard series is empty");
  let previous = "";
  points.forEach((raw) => {
    const point = record(raw, "series point");
    exact(point, ["session_date", "ticker_close", "benchmark_close", "ticker_normalized_return_pct", "benchmark_normalized_return_pct", "volume"], "series point");
    const session = isoDate(point.session_date, "series session");
    const tickerClose = finite(point.ticker_close, "series ticker close", false)!;
    const benchmarkClose = finite(point.benchmark_close, "series benchmark close", false)!;
    finite(point.ticker_normalized_return_pct, "series ticker return", false);
    finite(point.benchmark_normalized_return_pct, "series benchmark return", false);
    const volume = finite(point.volume, "series volume", false)!;
    if (session <= previous || session > resolved || tickerClose <= 0 || benchmarkClose <= 0 || volume < 0) throw Error("Invalid series chronology or values");
    previous = session;
  });
  const first = record(points[0], "first series point");
  const last = record(points.at(-1), "last series point");
  if (previous !== resolved || Math.abs(first.ticker_normalized_return_pct as number) > 1e-9 || Math.abs(first.benchmark_normalized_return_pct as number) > 1e-9) throw Error("Series does not bind to the resolved session");
  if (selectedRow.close !== null && Math.abs((last.ticker_close as number) - selectedRow.close) > 1e-8) throw Error("Series close does not match watchlist");
  return item as unknown as DashboardSeries;
}

function decodeEvidence(value: unknown, cutoff: string): DashboardEvidence[] {
  const rows = array(value, "dashboard evidence", 4);
  const ids = new Set<string>();
  return rows.map((raw) => {
    const item = record(raw, "dashboard evidence item");
    exact(item, ["citation_id", "title", "url", "source_type", "published_at", "available_at", "excerpt"], "dashboard evidence item");
    const citationId = safeText(item.citation_id, "citation ID", 80);
    if (!/^cit-[a-f0-9]{12,64}$/.test(citationId) || ids.has(citationId)) throw Error("Invalid or duplicate citation ID");
    ids.add(citationId);
    safeText(item.title, "evidence title", 500);
    publicHttps(item.url);
    member(item.source_type, SOURCE_TYPES, "evidence source type");
    const published = utcTimestamp(item.published_at, "published timestamp");
    const available = utcTimestamp(item.available_at, "available timestamp");
    safeText(item.excerpt, "evidence excerpt", 1_200);
    if (Date.parse(published) > Date.parse(available) || Date.parse(available) > Date.parse(cutoff)) throw Error("Evidence violates cutoff");
    return item as unknown as DashboardEvidence;
  });
}

function decodeReceipts(value: unknown, selected: PrimaryTicker): DashboardReceipt[] {
  const rows = array(value, "dashboard receipts", 20);
  if (!rows.length) throw Error("Dashboard receipts are required");
  const identities = new Set<string>();
  const decoded = rows.map((raw) => {
    const item = record(raw, "dashboard receipt");
    exact(item, ["operation", "ticker", "engine", "device", "gpu_executed", "fallback_used", "duration_ms"], "dashboard receipt");
    const operation = member(item.operation, OPERATIONS, "receipt operation");
    const ticker = member(item.ticker, INSTRUMENTS, "receipt ticker");
    const engine = member(item.engine, ENGINES, "receipt engine");
    safeText(item.device, "receipt device", 160);
    if (typeof item.gpu_executed !== "boolean" || item.fallback_used !== false) throw Error("Invalid receipt execution state");
    const duration = finite(item.duration_ms, "receipt duration", false)!;
    if (duration < 0 || (engine === "deterministic") !== !item.gpu_executed) throw Error("Inconsistent receipt execution state");
    const identity = `${operation}:${ticker}`;
    if (identities.has(identity)) throw Error("Duplicate dashboard receipt");
    identities.add(identity);
    return item as unknown as DashboardReceipt;
  });
  if (!decoded.some((receipt) => receipt.operation === "market_series" && receipt.ticker === selected)) throw Error("Selected series receipt is missing");
  for (const ticker of PRIMARY_TICKERS) {
    if (!decoded.some((receipt) => receipt.operation === "get_price_context" && receipt.ticker === ticker)) throw Error("Watchlist receipt is missing");
  }
  return decoded;
}

function decodeLimitations(value: unknown): DashboardLimitation[] {
  const rows = array(value, "dashboard limitations", 20);
  const codes = new Set<string>();
  return rows.map((raw) => {
    const item = record(raw, "dashboard limitation");
    exact(item, ["code", "message", "affected"], "dashboard limitation");
    const code = safeText(item.code, "limitation code", 64);
    if (!/^[a-z][a-z0-9_]*$/.test(code) || codes.has(code)) throw Error("Invalid or duplicate limitation code");
    codes.add(code);
    safeText(item.message, "limitation message", 500);
    const affected = array(item.affected, "limitation affected list", 20);
    if (affected.some((entry) => typeof entry !== "string" || entry.length < 1 || entry.length > 160 || entry !== entry.trim() || UNSAFE_TEXT_RE.test(entry)) || new Set(affected).size !== affected.length) throw Error("Invalid limitation affected list");
    return item as unknown as DashboardLimitation;
  });
}

export function decodeMarketDashboard(value: unknown): MarketDashboardData {
  const item = record(value, "dashboard response");
  exact(item, ROOT_KEYS, "dashboard response");
  if (item.schema_version !== "market-dashboard-v1" || item.mode !== "historical_reconstruction") throw Error("Invalid dashboard contract identity");
  const requested = isoDate(item.requested_as_of, "requested date");
  const resolved = isoDate(item.resolved_session, "resolved session");
  const cutoff = utcTimestamp(item.cutoff_at, "dashboard cutoff");
  const selected = member(item.selected_ticker, PRIMARY_TICKERS, "selected ticker");
  const coverage = decodeCoverage(item.coverage);
  if (requested < coverage.first_session || requested > coverage.last_session || resolved < coverage.first_session || resolved > requested || cutoff.slice(0, 10) !== resolved) throw Error("Invalid dashboard temporal scope");
  const watchlist = decodeWatchlist(item.watchlist, coverage);
  if (watchlist.some((row) => row.resolved_session !== resolved)) throw Error("Watchlist session does not match dashboard");
  decodeSeries(item.series, selected, resolved, watchlist);
  decodeEvidence(item.evidence, cutoff);
  decodeReceipts(item.receipts, selected);
  decodeLimitations(item.limitations);
  return item as unknown as MarketDashboardData;
}

const REQUEST_TIMEOUT_MS = 20_000;

function boundedDetail(value: unknown): string | null {
  if (typeof value !== "string" || value.length < 1 || value.length > 240 || value !== value.trim() || UNSAFE_TEXT_RE.test(value)) return null;
  return value;
}

export async function getMarketDashboard(ticker: PrimaryTicker, asOf: string, signal?: AbortSignal): Promise<MarketDashboardData> {
  if (!TICKERS.includes(ticker)) throw Error("Unsupported dashboard ticker");
  isoDate(asOf, "dashboard request date");
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort(); else signal?.addEventListener("abort", abort, { once: true });
  const timeout = setTimeout(abort, REQUEST_TIMEOUT_MS);
  try {
    const query = new URLSearchParams({ ticker, as_of: asOf });
    const response = await fetch(`/api/dashboard?${query.toString()}`, { headers: { Accept: "application/json" }, signal: controller.signal });
    if (!response.ok) {
      let detail: string | null = null;
      try { detail = boundedDetail((await response.json() as { detail?: unknown }).detail); } catch { /* use stable status fallback */ }
      throw Error(detail ?? `Dashboard unavailable (${response.status})`);
    }
    return decodeMarketDashboard(await response.json());
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}
