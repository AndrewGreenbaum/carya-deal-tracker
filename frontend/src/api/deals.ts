import type { Deal, InvestorRole, InvestmentStage } from '../types';

interface DealApiResponse {
  id: string;
  startup_name: string;
  investor_roles: string[];
  investment_stage: string;
  amount_invested: string;
  date: string;
  next_steps?: string;
}

interface PaginatedDealsResponse {
  deals: DealApiResponse[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

// Simple in-memory cache
const cache: Map<string, { data: any; timestamp: number }> = new Map();
const CACHE_TTL = 60000; // 1 minute

function getCached<T>(key: string): T | null {
  const cached = cache.get(key);
  if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
    return cached.data as T;
  }
  cache.delete(key);
  return null;
}

function setCache(key: string, data: any): void {
  cache.set(key, { data, timestamp: Date.now() });
}

export function invalidateCache(): void {
  cache.clear();
}

const API_BASE = '/api';

export interface FetchDealsOptions {
  limit?: number;
  offset?: number;
  stage?: string;
  is_lead?: boolean;
  fund_slug?: string;
}

export interface PaginatedDeals {
  deals: Deal[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}

export async function fetchDeals(options: FetchDealsOptions = {}): Promise<PaginatedDeals> {
  const { limit = 50, offset = 0, stage, is_lead, fund_slug } = options;

  // Build query string
  const params = new URLSearchParams();
  params.set('limit', limit.toString());
  params.set('offset', offset.toString());
  if (stage) params.set('stage', stage);
  if (is_lead !== undefined) params.set('is_lead', is_lead.toString());
  if (fund_slug) params.set('fund_slug', fund_slug);

  const cacheKey = `deals:${params.toString()}`;
  const cached = getCached<PaginatedDeals>(cacheKey);
  if (cached) {
    return cached;
  }

  const response = await fetch(`${API_BASE}/deals?${params.toString()}`);

  if (!response.ok) {
    throw new Error(`Failed to fetch deals: ${response.statusText}`);
  }

  const data: PaginatedDealsResponse = await response.json();

  const result: PaginatedDeals = {
    deals: data.deals.map((deal) => ({
      id: deal.id,
      startupName: deal.startup_name,
      investorRoles: deal.investor_roles as InvestorRole[],
      investmentStage: deal.investment_stage as InvestmentStage,
      amountInvested: deal.amount_invested,
      date: deal.date,
      nextSteps: deal.next_steps,
    })),
    total: data.total,
    limit: data.limit,
    offset: data.offset,
    hasMore: data.has_more,
  };

  setCache(cacheKey, result);
  return result;
}

// Legacy function for backwards compatibility
export async function fetchDealsLegacy(): Promise<Deal[]> {
  const result = await fetchDeals({ limit: 200 });
  return result.deals;
}

export async function fetchFunds() {
  const cacheKey = 'funds';
  const cached = getCached(cacheKey);
  if (cached) {
    return cached;
  }

  const response = await fetch(`${API_BASE}/funds`);

  if (!response.ok) {
    throw new Error(`Failed to fetch funds: ${response.statusText}`);
  }

  const data = await response.json();
  setCache(cacheKey, data);
  return data;
}

export async function fetchHealth() {
  const response = await fetch(`${API_BASE}/health`);

  if (!response.ok) {
    throw new Error(`Failed to fetch health: ${response.statusText}`);
  }

  return response.json();
}
