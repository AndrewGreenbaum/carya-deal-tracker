/**
 * App.tsx - Main orchestrator for the Bud Tracker Command Center
 *
 * Features:
 * - Dashboard view with stats, filters, and deals table
 * - Stealth Tracker view for silent portfolio additions
 * - Scraper Control view for manual scraper runs
 * - Settings panel for notifications
 * - Live system status indicators
 *
 * Command Center dark theme
 */

import { useState, useEffect, useCallback } from 'react';
import { CommandHeader } from './components/CommandHeader';
import { CommandSidebar } from './components/CommandSidebar';
import { StatsCards } from './components/StatsCards';
import { DealsTable } from './components/DealsTable';
import { DealModal } from './components/DealModal';
import { StealthTracker } from './components/StealthTracker';
import { ScraperControl } from './components/ScraperControl';
import { SettingsPanel } from './components/SettingsPanel';
import {
  fetchDeals,
  fetchFunds,
  fetchHealth,
  fetchScraperStatus,
  fetchSchedulerStatus,
  invalidateCache,
  getExportUrl,
} from './api/deals';
import type {
  Deal,
  Fund,
  FilterState,
  ViewType,
  SystemStatus,
  DashboardStats,
  CostTracker,
  PaginatedDeals,
} from './types';

const PAGE_SIZE = 50;

// Sample deals fallback
const sampleDeals: Deal[] = [
  {
    id: '1',
    startupName: 'VectorDB Labs',
    investorRoles: ['lead'],
    investmentStage: 'series_a',
    amountInvested: '$45M',
    date: '2025-01-15',
    enterpriseCategory: 'infrastructure',
    isEnterpriseAi: true,
    leadInvestor: 'Sequoia Capital',
    leadPartner: 'Roelof Botha',
    verificationSnippet: 'led by Sequoia Capital',
    sourceName: 'TechCrunch',
  },
  {
    id: '2',
    startupName: 'SecureAI',
    investorRoles: ['lead'],
    investmentStage: 'series_b',
    amountInvested: '$75M',
    date: '2025-02-20',
    enterpriseCategory: 'security',
    isEnterpriseAi: true,
    leadInvestor: 'a16z',
    verificationSnippet: 'Series B led by Andreessen Horowitz',
    sourceName: 'Fortune',
  },
  {
    id: '3',
    startupName: 'AgentFlow',
    investorRoles: ['lead', 'non_lead'],
    investmentStage: 'seed',
    amountInvested: '$12M',
    date: '2025-03-01',
    enterpriseCategory: 'agentic',
    isEnterpriseAi: true,
    leadInvestor: 'Founders Fund',
    leadPartner: 'Keith Rabois',
    verificationSnippet: 'Founders Fund led the round',
    sourceName: 'SEC EDGAR',
  },
];

function App() {
  // View state
  const [currentView, setCurrentView] = useState<ViewType>('dashboard');
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Data state
  const [deals, setDeals] = useState<Deal[]>([]);
  const [funds, setFunds] = useState<Fund[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Pagination state
  const [pagination, setPagination] = useState<PaginatedDeals>({
    deals: [],
    total: 0,
    limit: PAGE_SIZE,
    offset: 0,
    hasMore: false,
  });

  // Filter state - default to Enterprise AI + Lead only
  const [filters, setFilters] = useState<FilterState>({
    enterpriseAiOnly: true,
    leadOnly: true,
    showRejected: false,
    selectedFund: undefined,
    selectedCategory: undefined,
  });

  // System status
  const [systemStatus, setSystemStatus] = useState<SystemStatus>({
    secEdgar: { status: 'idle', lastRun: undefined },
    scrapers: { status: 'idle', lastRun: undefined },
    claude: { status: 'idle', lastRun: undefined },
    nextScrape: new Date(Date.now() + 3600000).toISOString(),
  });

  // Stats
  const [stats, setStats] = useState<DashboardStats>({
    newDeals24h: 0,
    newDealsTrend: 0,
    enterpriseAiRatio: 0,
    tokensUsed: '0',
    claudeCalls: 0,
    verificationRate: 100,
  });

  // Cost tracker
  const [costTracker] = useState<CostTracker>({
    currentMonth: 45,
    monthlyLimit: 100,
    lastUpdated: new Date().toISOString(),
  });

  // Modal state
  const [selectedDeal, setSelectedDeal] = useState<Deal | null>(null);

  // Load system status
  const loadSystemStatus = useCallback(async () => {
    try {
      const [health, scraperStatus, schedulerStatus] = await Promise.all([
        fetchHealth().catch(() => null),
        fetchScraperStatus().catch(() => null),
        fetchSchedulerStatus().catch(() => null),
      ]);

      if (health || scraperStatus || schedulerStatus) {
        setSystemStatus((prev) => ({
          ...prev,
          secEdgar: { status: 'live', lastRun: new Date().toISOString() },
          scrapers: {
            status: scraperStatus?.implemented?.length ? 'live' : 'idle',
            lastRun: new Date().toISOString(),
          },
          claude: { status: 'live', lastRun: new Date().toISOString() },
          nextScrape: schedulerStatus?.next_run || prev.nextScrape,
        }));
      }
    } catch (err) {
      console.warn('Failed to load system status:', err);
    }
  }, []);

  // Load funds
  const loadFunds = useCallback(async () => {
    try {
      const fundsData = await fetchFunds();
      setFunds(fundsData);
    } catch (err) {
      console.warn('Failed to load funds:', err);
    }
  }, []);

  // Load deals
  const loadDeals = useCallback(
    async (offset = 0) => {
      setLoading(true);
      try {
        // When showRejected is true, don't apply filters - show everything
        // Otherwise, apply Enterprise AI and Lead filters to API call
        const result = await fetchDeals({
          limit: PAGE_SIZE,
          offset,
          is_lead: filters.showRejected ? undefined : (filters.leadOnly ? true : undefined),
          is_enterprise_ai: filters.showRejected ? undefined : (filters.enterpriseAiOnly ? true : undefined),
          enterprise_category: filters.selectedCategory,
          fund_slug: filters.selectedFund,
        });

        // Use API results directly - no client-side filtering needed
        // The API already filters based on the parameters we sent
        setDeals(result.deals);
        setPagination({
          deals: result.deals,
          total: result.total,
          limit: PAGE_SIZE,
          offset: result.offset,
          hasMore: result.hasMore,
        });

        // Calculate stats from the results
        const now = new Date();
        const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
        const newDeals = result.deals.filter((d) => d.date && new Date(d.date) > yesterday).length;
        const enterpriseDeals = result.deals.filter((d) => d.isEnterpriseAi).length;
        const ratio = result.deals.length > 0
          ? Math.round((enterpriseDeals / result.deals.length) * 100)
          : 0;

        setStats((prev) => ({
          ...prev,
          newDeals24h: newDeals,
          enterpriseAiRatio: ratio,
          verificationRate: 100,
        }));

        setError(null);
      } catch (err) {
        console.warn('API unavailable, using sample data:', err);
        setDeals(sampleDeals);
        setPagination({
          deals: sampleDeals,
          total: sampleDeals.length,
          limit: PAGE_SIZE,
          offset: 0,
          hasMore: false,
        });
        setError('Using sample data (API unavailable)');
      } finally {
        setLoading(false);
      }
    },
    [filters]
  );

  // Initial load and reload on filter change
  useEffect(() => {
    loadDeals(0);
  }, [loadDeals]);

  // Load funds and system status once on mount
  useEffect(() => {
    loadFunds();
    loadSystemStatus();
  }, [loadFunds, loadSystemStatus]);

  // Refresh system status periodically
  useEffect(() => {
    const interval = setInterval(loadSystemStatus, 60000);
    return () => clearInterval(interval);
  }, [loadSystemStatus]);

  const handleRefresh = () => {
    invalidateCache();
    loadDeals(0);
    loadSystemStatus();
  };

  const handleExport = () => {
    const url = getExportUrl({
      is_lead: filters.leadOnly ? true : undefined,
      is_enterprise_ai: filters.enterpriseAiOnly ? true : undefined,
      enterprise_category: filters.selectedCategory,
      fund_slug: filters.selectedFund,
    });
    window.open(url, '_blank');
  };

  const handlePageChange = (newOffset: number) => {
    loadDeals(newOffset);
  };

  return (
    <div className="h-screen flex flex-col bg-[#050506] text-slate-300 font-['JetBrains_Mono',monospace]">
      {/* Header */}
      <CommandHeader
        systemStatus={systemStatus}
        onRefresh={handleRefresh}
        onSettingsClick={() => setSettingsOpen(true)}
      />

      {/* Main Layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar */}
        <CommandSidebar
          filters={filters}
          onFiltersChange={setFilters}
          funds={funds}
          currentView={currentView}
          onViewChange={setCurrentView}
          costTracker={costTracker}
        />

        {/* Main Content */}
        <main className="flex-1 flex flex-col overflow-hidden">
          {/* Dashboard View */}
          {currentView === 'dashboard' && (
            <>
              {/* Stats Cards */}
              <StatsCards stats={stats} />

              {/* Error Banner */}
              {error && (
                <div className="px-6 py-2 border-b border-amber-500/30 bg-amber-500/10 text-amber-400 text-sm flex items-center justify-between">
                  <span>{error}</span>
                  <button
                    onClick={handleRefresh}
                    className="text-amber-300 hover:text-amber-200 underline"
                  >
                    Retry
                  </button>
                </div>
              )}

              {/* Deals Table */}
              <div className="flex-1 flex flex-col overflow-hidden">
                <DealsTable
                  deals={deals}
                  pagination={pagination}
                  onDealClick={setSelectedDeal}
                  onExport={handleExport}
                  onRefresh={handleRefresh}
                  onPageChange={handlePageChange}
                  isLoading={loading}
                />
              </div>
            </>
          )}

          {/* Stealth Tracker View */}
          {currentView === 'stealth' && <StealthTracker />}

          {/* Scrapers View */}
          {currentView === 'scrapers' && <ScraperControl />}
        </main>
      </div>

      {/* Deal Modal */}
      {selectedDeal && (
        <DealModal deal={selectedDeal} onClose={() => setSelectedDeal(null)} />
      )}

      {/* Settings Panel */}
      <SettingsPanel isOpen={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}

export default App;
