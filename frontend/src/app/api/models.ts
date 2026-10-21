/**
 * Names for the generated API types (schema.d.ts, from FastAPI's OpenAPI via `make api-types`).
 * Aliases only: no hand-written API shapes in the frontend.
 */
import type { components } from './schema';

type Schemas = components['schemas'];

export type Brief = Schemas['BriefOut'];
export type RankedSignal = Schemas['RankedSignalOut'];
export type Signal = Schemas['SignalOut'];
export type SignalPage = Schemas['SignalPage'];
export type Evidence = Schemas['EvidenceOut'];
export type Confidence = Schemas['ConfidenceOut'];
export type EntityRef = Schemas['EntityRef'];
export type SourceRef = Schemas['SourceOut'];
export type SinceYesterdayItem = Schemas['SinceYesterdayItem'];
export type PricingItem = Schemas['PricingItem'];
export type PriceChange = Schemas['PriceChange'];
export type PipelineInfo = Schemas['PipelineInfo'];
export type Entity = Schemas['EntityOut'];
export type NavCounts = Schemas['NavCounts'];
export type SignalCategory = Schemas['SignalCategory'];
export type Verification = Schemas['Verification'];
