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
export type SelfInfo = Schemas['SelfOut'];
export type SignalCategory = Schemas['SignalCategory'];
export type Verification = Schemas['Verification'];
export type CitationRef = Schemas['CitationRef'];
export type TopStory = Schemas['TopStoryOut'];
export type StoryRow = Schemas['StoryRowOut'];
export type BriefStats = Schemas['BriefStats'];
export type Battlecard = Schemas['BattlecardOut'];
export type RadarTeaser = Schemas['RadarTeaser'];
export type RadarCandidate = Schemas['RadarCandidateOut'];
export type RadarCandidateDetail = Schemas['RadarCandidateDetail'];
export type RadarList = Schemas['RadarListOut'];
export type RadarBreakdown = Schemas['RadarBreakdown'];
export type RadarScoreComponent = Schemas['RadarScoreComponent'];
export type RadarStatement = Schemas['RadarStatementOut'];
export type RadarMention = Schemas['RadarMentionOut'];
export type RadarApproveIn = Schemas['RadarApproveIn'];
export type RadarApproveOut = Schemas['RadarApproveOut'];
export type RadarDismissIn = Schemas['RadarDismissIn'];
export type BattlecardListItem = Schemas['BattlecardListItem'];
export type MatrixRow = Schemas['MatrixRow'];
export type CitedText = Schemas['CitedText'];
export type TimelineItem = Schemas['TimelineItem'];
export type CapabilityLevel = Schemas['JFrogLevel']['level'];
