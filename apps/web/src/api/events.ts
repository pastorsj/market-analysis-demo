export type EventCapability = "move-measurement" | "evidence-review" | "peer-comparison" | "historical-analogues" | "shock-propagation" | "risk-analysis";
export type EventLayer = "market" | "documents" | "licensed_news" | "derived_features";
export type EventLayerStatus = "ready" | "partial" | "blocked";

export interface ShockEventCategory {
  readonly category_id: string;
  readonly label: string;
  readonly description: string;
  readonly sort_order: number;
}

export interface ShockEventQuestion {
  readonly question_id: string;
  readonly label: string;
  readonly capability: EventCapability;
  readonly text: string;
}

export interface ShockEventGap {
  readonly code: string;
  readonly layer: EventLayer;
  readonly detail: string;
}

export interface ShockEventLayerQualification {
  readonly status: EventLayerStatus;
  readonly gaps: readonly ShockEventGap[];
}

export interface ShockEventQualification {
  readonly status: "ready" | "partial";
  readonly market: ShockEventLayerQualification;
  readonly documents: ShockEventLayerQualification;
  readonly licensed_news: ShockEventLayerQualification;
  readonly derived_features: ShockEventLayerQualification;
  readonly gaps: readonly ShockEventGap[];
}

export interface ShockEventSourceRequirements {
  readonly market: { readonly required: true; readonly required_fields: readonly ("adjusted_close" | "volume")[]; readonly price_basis: "provider_adjusted" };
  readonly documents: { readonly required_for_ready: true; readonly requirement_id: string; readonly source_kinds: readonly ("company_release" | "filing" | "primary_source")[] };
  readonly licensed_news: { readonly required_for_publication: false; readonly required_for_ready: true; readonly source_kind: "licensed_news_metadata" };
  readonly derived_features: { readonly required_for_ready: true; readonly features: readonly ("event-returns" | "relative-returns" | "volume-context" | "historical-analogues")[] };
}

export interface ShockEventLimitation {
  readonly limitation_id: string;
  readonly detail: string;
}

export interface ShockEvent {
  readonly event_id: string;
  readonly category_id: string;
  readonly title: string;
  readonly summary: string;
  readonly sort_order: number;
  readonly event_session: string;
  readonly source_dates: readonly string[];
  readonly primary_ticker: string;
  readonly analysis_tickers: readonly string[];
  readonly context_instruments: readonly string[];
  readonly start_session: string;
  readonly end_session: string;
  readonly default_cutoff: string;
  readonly questions: readonly ShockEventQuestion[];
  readonly source_requirements: ShockEventSourceRequirements;
  readonly limitations: readonly ShockEventLimitation[];
  readonly qualification: ShockEventQualification;
}

export interface ShockEventSummary {
  readonly declared_events: number;
  readonly published_events: number;
  readonly ready_events: number;
  readonly partial_events: number;
  readonly excluded_events: number;
}

export interface ShockEventCatalog {
  readonly schema_version: "shock-event-catalog-v1";
  readonly catalog_id: string;
  readonly catalog_sha256: string;
  readonly scenario_id: string;
  readonly scenario_manifest_sha256: string;
  readonly calendar: "XNYS";
  readonly timezone: "America/New_York";
  readonly supported_tickers: readonly string[];
  readonly categories: readonly ShockEventCategory[];
  readonly events: readonly ShockEvent[];
  readonly summary: ShockEventSummary;
}

const ROOT_KEYS = ["schema_version", "catalog_id", "catalog_sha256", "scenario_id", "scenario_manifest_sha256", "calendar", "timezone", "supported_tickers", "categories", "events", "summary"] as const;
const EVENT_KEYS = ["event_id", "category_id", "title", "summary", "sort_order", "event_session", "source_dates", "primary_ticker", "analysis_tickers", "context_instruments", "start_session", "end_session", "default_cutoff", "questions", "source_requirements", "limitations", "qualification"] as const;
const ID_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const TICKER_RE = /^[A-Z][A-Z0-9.-]{0,9}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DIGEST_RE = /^[a-f0-9]{64}$/;
const UTC_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const UNSAFE_RE = /(?:\bllama(?:\b|[-_/])|(?:https?|grpc):\/\/|\bbearer\s+[A-Za-z0-9._-]{6,}|\b(?:api[_ -]?key|authorization|password|secret|access[_ -]?token)\b\s*[:=])/i;
const CAPABILITIES = ["move-measurement", "evidence-review", "peer-comparison", "historical-analogues", "shock-propagation", "risk-analysis"] as const;
const LAYERS = ["market", "documents", "licensed_news", "derived_features"] as const;
const SOURCE_KINDS = ["company_release", "filing", "primary_source"] as const;
const FEATURE_NAMES = ["event-returns", "relative-returns", "volume-context", "historical-analogues"] as const;

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw Error(`Invalid ${label}`);
  return value as Record<string, unknown>;
}

function exact(value: Record<string, unknown>, keys: readonly string[], label: string): void {
  const actual = Object.keys(value).sort(), expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) throw Error(`Invalid ${label} fields`);
}

function list(value: unknown, label: string, minimum: number, maximum: number): unknown[] {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) throw Error(`Invalid ${label}`);
  return value;
}

function safeText(value: unknown, label: string, minimum: number, maximum: number): string {
  if (typeof value !== "string" || value.length < minimum || value.length > maximum || value !== value.trim() || UNSAFE_RE.test(value)) throw Error(`Invalid or unsafe ${label}`);
  return value;
}

function identifier(value: unknown, label: string, maximum = 96): string {
  const result = safeText(value, label, 3, maximum);
  if (!ID_RE.test(result)) throw Error(`Invalid ${label}`);
  return result;
}

function ticker(value: unknown, label: string): string {
  if (typeof value !== "string" || !TICKER_RE.test(value)) throw Error(`Invalid ${label}`);
  return value;
}

function date(value: unknown, label: string): string {
  if (typeof value !== "string" || !DATE_RE.test(value)) throw Error(`Invalid ${label}`);
  const [year, month, day] = value.split("-").map(Number);
  if (new Date(Date.UTC(year, month - 1, day)).toISOString().slice(0, 10) !== value) throw Error(`Invalid ${label}`);
  return value;
}

function instant(value: unknown, label: string): string {
  if (typeof value !== "string" || !UTC_RE.test(value) || !Number.isFinite(Date.parse(value))) throw Error(`Invalid ${label}`);
  return value;
}

function integer(value: unknown, label: string, minimum = 0, maximum = 1_000_000): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum || value > maximum) throw Error(`Invalid ${label}`);
  return value;
}

function member<T extends string>(value: unknown, allowed: readonly T[], label: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) throw Error(`Invalid ${label}`);
  return value as T;
}

function uniqueStrings(value: unknown, label: string, minimum: number, maximum: number, validate: (item: unknown, name: string) => string): string[] {
  const rows = list(value, label, minimum, maximum).map((item) => validate(item, label));
  if (new Set(rows).size !== rows.length) throw Error(`Duplicate ${label}`);
  return rows;
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function sortedGaps(gaps: readonly ShockEventGap[]): readonly ShockEventGap[] {
  return [...gaps].sort((left, right) =>
    left.layer.localeCompare(right.layer)
    || left.code.localeCompare(right.code)
    || left.detail.localeCompare(right.detail));
}

function decodeGap(value: unknown): ShockEventGap {
  const item = object(value, "event qualification gap");
  exact(item, ["code", "layer", "detail"], "event qualification gap");
  const code = safeText(item.code, "gap code", 1, 96);
  if (!/^[a-z][a-z0-9_]*$/.test(code)) throw Error("Invalid gap code");
  member(item.layer, LAYERS, "gap layer");
  safeText(item.detail, "gap detail", 10, 280);
  return item as unknown as ShockEventGap;
}

function decodeLayer(value: unknown, layer: EventLayer): ShockEventLayerQualification {
  const item = object(value, `${layer} qualification`);
  exact(item, ["status", "gaps"], `${layer} qualification`);
  const allowed = layer === "market" ? ["ready", "blocked"] as const : ["ready", "partial"] as const;
  const status = member(item.status, allowed, `${layer} status`);
  const gaps = list(item.gaps, `${layer} gaps`, 0, 20).map(decodeGap);
  if (gaps.some((gap) => gap.layer !== layer) || (status === "ready") !== (gaps.length === 0)) throw Error(`Inconsistent ${layer} qualification`);
  return item as unknown as ShockEventLayerQualification;
}

function decodeQualification(value: unknown): ShockEventQualification {
  const item = object(value, "event qualification");
  exact(item, ["status", "market", "documents", "licensed_news", "derived_features", "gaps"], "event qualification");
  const status = member(item.status, ["ready", "partial"] as const, "event qualification status");
  const layers = LAYERS.map((layer) => decodeLayer(item[layer], layer));
  const gaps = list(item.gaps, "event qualification gaps", 0, 80).map(decodeGap);
  const flattened = layers.flatMap((layer) => layer.gaps);
  const identities = gaps.map((gap) => `${gap.layer}\u0000${gap.code}\u0000${gap.detail}`);
  if (new Set(identities).size !== gaps.length || !same(sortedGaps(gaps), sortedGaps(flattened)) || layers[0].status !== "ready" || (status === "ready") !== layers.every((layer) => layer.status === "ready")) throw Error("Inconsistent event qualification");
  return item as unknown as ShockEventQualification;
}

function decodeSourceRequirements(value: unknown): ShockEventSourceRequirements {
  const item = object(value, "event source requirements");
  exact(item, ["market", "documents", "licensed_news", "derived_features"], "event source requirements");
  const market = object(item.market, "market requirement");
  exact(market, ["required", "required_fields", "price_basis"], "market requirement");
  const fields = uniqueStrings(market.required_fields, "required market fields", 1, 2, (entry, label) => member(entry, ["adjusted_close", "volume"] as const, label));
  if (market.required !== true || market.price_basis !== "provider_adjusted" || !same(fields, [...fields].sort())) throw Error("Invalid market requirement");
  const documents = object(item.documents, "document requirement");
  exact(documents, ["required_for_ready", "requirement_id", "source_kinds"], "document requirement");
  identifier(documents.requirement_id, "document requirement ID");
  const sources = uniqueStrings(documents.source_kinds, "document source kinds", 1, 3, (entry, label) => member(entry, SOURCE_KINDS, label));
  if (documents.required_for_ready !== true || !same(sources, [...sources].sort())) throw Error("Invalid document requirement");
  const news = object(item.licensed_news, "licensed-news requirement");
  exact(news, ["required_for_publication", "required_for_ready", "source_kind"], "licensed-news requirement");
  if (news.required_for_publication !== false || news.required_for_ready !== true || news.source_kind !== "licensed_news_metadata") throw Error("Invalid licensed-news requirement");
  const derived = object(item.derived_features, "derived-feature requirement");
  exact(derived, ["required_for_ready", "features"], "derived-feature requirement");
  const features = uniqueStrings(derived.features, "derived features", 1, 4, (entry, label) => member(entry, FEATURE_NAMES, label));
  if (derived.required_for_ready !== true || !same(features, FEATURE_NAMES.filter((feature) => features.includes(feature)))) throw Error("Invalid derived-feature requirement");
  return item as unknown as ShockEventSourceRequirements;
}

function decodeEvent(value: unknown, categoryIds: ReadonlySet<string>): ShockEvent {
  const item = object(value, "shock event");
  exact(item, EVENT_KEYS, "shock event");
  identifier(item.event_id, "event ID");
  const category = identifier(item.category_id, "event category ID");
  if (!categoryIds.has(category)) throw Error("Event uses an unknown category");
  safeText(item.title, "event title", 5, 100);
  safeText(item.summary, "event summary", 20, 420);
  integer(item.sort_order, "event sort order", 1, 10_000);
  const eventSession = date(item.event_session, "event session");
  const sourceDates = uniqueStrings(item.source_dates, "event source dates", 1, 8, date);
  const primary = ticker(item.primary_ticker, "event primary ticker");
  const analysis = uniqueStrings(item.analysis_tickers, "analysis tickers", 1, 5, ticker);
  const context = uniqueStrings(item.context_instruments, "context instruments", 1, 4, ticker);
  if (analysis[0] !== primary || analysis.some((symbol) => context.includes(symbol))) throw Error("Invalid event instrument scope");
  const start = date(item.start_session, "event start session");
  const end = date(item.end_session, "event end session");
  const cutoff = instant(item.default_cutoff, "event cutoff");
  if (start > eventSession || eventSession > end || cutoff.slice(0, 10) < eventSession || cutoff.slice(0, 10) > end || sourceDates.some((value) => value < start || value > cutoff.slice(0, 10))) throw Error("Invalid event chronology");
  const questions = list(item.questions, "event questions", 3, 6);
  const questionIds = new Set<string>();
  for (const raw of questions) {
    const question = object(raw, "event question");
    exact(question, ["question_id", "label", "capability", "text"], "event question");
    const questionId = identifier(question.question_id, "question ID");
    if (questionIds.has(questionId)) throw Error("Duplicate question ID");
    questionIds.add(questionId);
    safeText(question.label, "question label", 3, 72);
    member(question.capability, CAPABILITIES, "question capability");
    safeText(question.text, "question text", 12, 500);
  }
  decodeSourceRequirements(item.source_requirements);
  const limitations = list(item.limitations, "event limitations", 1, 20);
  const limitationIds = new Set<string>();
  for (const raw of limitations) {
    const limitation = object(raw, "event limitation");
    exact(limitation, ["limitation_id", "detail"], "event limitation");
    const limitationId = identifier(limitation.limitation_id, "limitation ID");
    if (limitationIds.has(limitationId)) throw Error("Duplicate limitation ID");
    limitationIds.add(limitationId);
    safeText(limitation.detail, "limitation detail", 10, 280);
  }
  decodeQualification(item.qualification);
  return item as unknown as ShockEvent;
}

export function decodeShockEventCatalog(value: unknown): ShockEventCatalog {
  const item = object(value, "shock event catalog");
  exact(item, ROOT_KEYS, "shock event catalog");
  if (item.schema_version !== "shock-event-catalog-v1" || item.calendar !== "XNYS" || item.timezone !== "America/New_York") throw Error("Invalid shock event catalog identity");
  identifier(item.catalog_id, "catalog ID", 160);
  safeText(item.scenario_id, "event scenario ID", 3, 160);
  if (typeof item.catalog_sha256 !== "string" || !DIGEST_RE.test(item.catalog_sha256) || typeof item.scenario_manifest_sha256 !== "string" || !DIGEST_RE.test(item.scenario_manifest_sha256)) throw Error("Invalid event catalog digest");
  const categories = list(item.categories, "event categories", 1, 32);
  const categoryIds = new Set<string>();
  let priorCategoryOrder = 0;
  for (const raw of categories) {
    const category = object(raw, "event category");
    exact(category, ["category_id", "label", "description", "sort_order"], "event category");
    const categoryId = identifier(category.category_id, "category ID");
    const order = integer(category.sort_order, "category sort order", 1, 32);
    if (categoryIds.has(categoryId) || order <= priorCategoryOrder) throw Error("Invalid category order");
    categoryIds.add(categoryId); priorCategoryOrder = order;
    safeText(category.label, "category label", 3, 64);
    safeText(category.description, "category description", 10, 240);
  }
  const events = list(item.events, "shock events", 1, 1_000).map((event) => decodeEvent(event, categoryIds));
  const questionIds = events.flatMap((event) => event.questions.map((question) => question.question_id));
  if (new Set(events.map((event) => event.event_id)).size !== events.length || new Set(questionIds).size !== questionIds.length || events.some((event, index) => index > 0 && event.sort_order <= events[index - 1].sort_order)) throw Error("Invalid event order or identity");
  const supported = uniqueStrings(item.supported_tickers, "event supported tickers", 1, 5_000, ticker);
  const derived = [...new Set(events.flatMap((event) => event.analysis_tickers))].sort();
  if (!same(supported, derived)) throw Error("Event ticker catalog does not match published events");
  const summary = object(item.summary, "event catalog summary");
  exact(summary, ["declared_events", "published_events", "ready_events", "partial_events", "excluded_events"], "event catalog summary");
  const declared = integer(summary.declared_events, "declared event count");
  const published = integer(summary.published_events, "published event count");
  const ready = integer(summary.ready_events, "ready event count");
  const partial = integer(summary.partial_events, "partial event count");
  const excluded = integer(summary.excluded_events, "excluded event count");
  if (published !== events.length || ready !== events.filter((event) => event.qualification.status === "ready").length || partial !== events.length - ready || declared !== published + excluded) throw Error("Inconsistent event catalog summary");
  return item as unknown as ShockEventCatalog;
}

const TIMEOUT_MS = 20_000;

function boundedDetail(value: unknown): string | null {
  return typeof value === "string" && value.length >= 1 && value.length <= 240 && value === value.trim() && !UNSAFE_RE.test(value) ? value : null;
}

export async function getShockEvents(signal?: AbortSignal): Promise<ShockEventCatalog> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort(); else signal?.addEventListener("abort", abort, { once: true });
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, TIMEOUT_MS);
  try {
    const response = await fetch("/api/shock-events", { headers: { Accept: "application/json" }, signal: controller.signal });
    if (!response.ok) {
      let detail: string | null = null;
      try { detail = boundedDetail((await response.json() as { detail?: unknown }).detail); } catch { /* use stable status fallback */ }
      throw Error(detail ?? `Event catalog unavailable (${response.status})`);
    }
    return decodeShockEventCatalog(await response.json());
  } catch (reason) {
    if (timedOut && !signal?.aborted) throw Error("Event catalog request timed out.");
    throw reason;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}
