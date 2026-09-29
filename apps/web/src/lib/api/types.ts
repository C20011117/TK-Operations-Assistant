import type { components } from "./schema";

type S = components["schemas"];

export type Market = S["Market"];
export type JobView = S["JobView"];
export type Health = S["Health"];
export type AppInfo = S["AppInfo"];
export type SettingsView = S["SettingsView"];
export type ExternalChecks = S["ExternalChecks"];
export type ExternalCheck = S["ExternalCheck"];

export type ProductSummary = S["ProductSummary"];
export type ProductDetail = S["ProductDetail"];
export type ProductVersion = S["ProductVersion"];
export type ProductFacts = S["ProductFacts"];
export type MarketTerm = S["MarketTerm"];
export type MarketTermIn = S["MarketTermIn"];

export type CampaignSummary = S["CampaignSummary"];
export type CampaignDetail = S["CampaignDetail"];
export type CampaignMarketView = S["CampaignMarketView"];
export type Criterion = S["Criterion"];
export type CriteriaVersion = S["CriteriaVersion"];
export type FieldSpec = S["FieldSpec"];
export type Issue = S["Issue"];

export type RunView = S["RunView"];
export type RecommendationsView = S["RecommendationsView"];
export type RecommendationCard = S["RecommendationCard"];
export type CardPoint = S["CardPoint"];
export type CardNote = S["CardNote"];
