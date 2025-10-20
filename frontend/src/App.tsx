import { useState, useEffect, useCallback } from 'react';
import { Moon, Sun, Download } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { DealTable } from './components/DealTable';
import { DealModal } from './components/DealModal';
import { StealthTracker } from './components/StealthTracker';
import { fetchDeals, invalidateCache, getExportUrl, type FetchDealsOptions } from './api/deals';
import type { Deal } from './types';

// Sample data for fallback when API unavailable
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
  },
];

const PAGE_SIZE = 50;

function App() {
  const [activeMenuItem, setActiveMenuItem] = useState('home');
  const [deals, setDeals] = useState<Deal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [darkMode, setDarkMode] = useState(false);

  // Pagination state
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);

  // Filter state - default to Enterprise AI + Lead only
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [stageFilter, _setStageFilter] = useState<string | undefined>();
  const [leadFilter, setLeadFilter] = useState<boolean | undefined>(true);
  const [enterpriseFilter, setEnterpriseFilter] = useState<boolean | undefined>(true);
  const [categoryFilter, setCategoryFilter] = useState<string | undefined>();

  // Modal state
  const [selectedDeal, setSelectedDeal] = useState<Deal | null>(null);

  const loadDeals = useCallback(async (options: FetchDealsOptions = {}) => {
    setLoading(true);
    try {
      const result = await fetchDeals({
        limit: PAGE_SIZE,
        offset: options.offset ?? offset,
        stage: options.stage ?? stageFilter,
        is_lead: options.is_lead ?? leadFilter,
        is_enterprise_ai: options.is_enterprise_ai ?? enterpriseFilter,
        enterprise_category: options.enterprise_category ?? categoryFilter,
      });

      setDeals(result.deals);
      setTotal(result.total);
      setOffset(result.offset);
      setHasMore(result.hasMore);
      setError(null);
    } catch (err) {
      console.warn('API unavailable, using sample data:', err);
      setDeals(sampleDeals);
      setTotal(sampleDeals.length);
      setHasMore(false);
      setError('Using sample data (API unavailable)');
    } finally {
      setLoading(false);
    }
  }, [offset, stageFilter, leadFilter, enterpriseFilter, categoryFilter]);

  useEffect(() => {
    loadDeals({ offset: 0 });
  }, [stageFilter, leadFilter, enterpriseFilter, categoryFilter]);

  // Initial load
  useEffect(() => {
    loadDeals({ offset: 0 });
  }, []);

  const handleDealClick = (deal: Deal) => {
    setSelectedDeal(deal);
  };

  const handleExport = () => {
    const url = getExportUrl({
      stage: stageFilter,
      is_lead: leadFilter,
      is_enterprise_ai: enterpriseFilter,
      enterprise_category: categoryFilter,
    });
    window.open(url, '_blank');
  };

  const handleNextPage = () => {
    if (hasMore) {
      loadDeals({ offset: offset + PAGE_SIZE });
    }
  };

  const handlePrevPage = () => {
    if (offset > 0) {
      loadDeals({ offset: Math.max(0, offset - PAGE_SIZE) });
    }
  };

  const handleRefresh = () => {
    invalidateCache();
    loadDeals({ offset: 0 });
  };

  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className={`h-screen flex ${darkMode ? 'bg-black' : 'bg-white'}`}>
      {/* Sidebar */}
      <Sidebar
        activeItem={activeMenuItem}
        onItemClick={setActiveMenuItem}
        darkMode={darkMode}
      />

      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <Header darkMode={darkMode}>
          <button
            onClick={() => setDarkMode(!darkMode)}
            className={`p-2 rounded-lg transition-colors ${
              darkMode
                ? 'hover:bg-gray-800 text-gray-300'
                : 'hover:bg-gray-100 text-gray-600'
            }`}
            title={darkMode ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
          >
            {darkMode ? <Sun size={20} /> : <Moon size={20} />}
          </button>
        </Header>

        {/* Content Area */}
        <main className={`flex-1 overflow-hidden flex flex-col ${darkMode ? 'bg-black' : ''}`}>
          {/* Stealth Tracker View */}
          {activeMenuItem === 'stealth' ? (
            <StealthTracker darkMode={darkMode} />
          ) : (
          <>
          {/* Status Banner */}
          {error && (
            <div className={`px-6 py-2 border-b text-sm flex items-center justify-between ${
              darkMode
                ? 'bg-amber-900/30 border-amber-700 text-amber-400'
                : 'bg-amber-50 border-amber-200 text-amber-700'
            }`}>
              <span>{error}</span>
              <button
                onClick={handleRefresh}
                className={`underline text-sm ${
                  darkMode ? 'text-amber-300 hover:text-amber-200' : 'text-amber-800 hover:text-amber-900'
                }`}
              >
                Retry
              </button>
            </div>
          )}

          {/* Filter Controls */}
          <div className={`px-6 py-3 border-b flex items-center gap-4 ${
            darkMode
              ? 'bg-gray-900 border-gray-700'
              : 'bg-gray-50 border-gray-200'
          }`}>
            <span className={`text-sm font-medium ${darkMode ? 'text-gray-400' : 'text-gray-600'}`}>Filters:</span>
            <button
              onClick={() => setEnterpriseFilter(enterpriseFilter ? undefined : true)}
              className={`px-3 py-1 text-sm rounded-full border transition-colors ${
                enterpriseFilter
                  ? darkMode
                    ? 'bg-blue-900 border-blue-700 text-blue-300'
                    : 'bg-blue-100 border-blue-300 text-blue-700'
                  : darkMode
                    ? 'border-gray-600 text-gray-400 hover:border-gray-500'
                    : 'border-gray-300 text-gray-600 hover:border-gray-400'
              }`}
            >
              Enterprise AI Only
            </button>
            <button
              onClick={() => setLeadFilter(leadFilter ? undefined : true)}
              className={`px-3 py-1 text-sm rounded-full border transition-colors ${
                leadFilter
                  ? darkMode
                    ? 'bg-green-900 border-green-700 text-green-300'
                    : 'bg-green-100 border-green-300 text-green-700'
                  : darkMode
                    ? 'border-gray-600 text-gray-400 hover:border-gray-500'
                    : 'border-gray-300 text-gray-600 hover:border-gray-400'
              }`}
            >
              Lead Deals Only
            </button>
            <select
              value={categoryFilter || ''}
              onChange={(e) => setCategoryFilter(e.target.value || undefined)}
              className={`px-3 py-1 text-sm rounded border ${
                darkMode
                  ? 'bg-gray-800 border-gray-600 text-gray-300'
                  : 'bg-white border-gray-300 text-gray-700'
              }`}
            >
              <option value="">All Categories</option>
              <option value="infrastructure">Infrastructure</option>
              <option value="security">Security</option>
              <option value="vertical_saas">Vertical SaaS</option>
              <option value="agentic">Agentic</option>
              <option value="data_intelligence">Data Intelligence</option>
            </select>
          </div>

          {/* Pagination Controls */}
          {!loading && deals.length > 0 && (
            <div className={`px-6 py-3 border-b flex items-center justify-between ${
              darkMode
                ? 'bg-gray-900 border-gray-700'
                : 'bg-gray-50 border-gray-200'
            }`}>
              <div className={`text-sm ${darkMode ? 'text-gray-400' : 'text-gray-600'}`}>
                Showing {offset + 1}-{Math.min(offset + deals.length, total)} of {total} deals
              </div>
              <div className="flex items-center gap-4">
                <button
                  onClick={handleExport}
                  className={`flex items-center gap-1 text-sm ${darkMode ? 'text-green-400 hover:text-green-300' : 'text-green-600 hover:text-green-800'}`}
                  title="Export to CSV"
                >
                  <Download size={14} />
                  Export
                </button>
                <button
                  onClick={handleRefresh}
                  className={`text-sm ${darkMode ? 'text-blue-400 hover:text-blue-300' : 'text-blue-600 hover:text-blue-800'}`}
                >
                  Refresh
                </button>
                <div className="flex items-center gap-2">
                  <button
                    onClick={handlePrevPage}
                    disabled={offset === 0}
                    className={`px-3 py-1 text-sm rounded border ${
                      offset === 0
                        ? darkMode
                          ? 'border-gray-700 text-gray-600 cursor-not-allowed'
                          : 'border-gray-200 text-gray-400 cursor-not-allowed'
                        : darkMode
                          ? 'border-gray-600 text-gray-300 hover:bg-gray-800'
                          : 'border-gray-300 text-gray-700 hover:bg-gray-100'
                    }`}
                  >
                    Previous
                  </button>
                  <span className={`text-sm ${darkMode ? 'text-gray-400' : 'text-gray-600'}`}>
                    Page {currentPage} of {totalPages || 1}
                  </span>
                  <button
                    onClick={handleNextPage}
                    disabled={!hasMore}
                    className={`px-3 py-1 text-sm rounded border ${
                      !hasMore
                        ? darkMode
                          ? 'border-gray-700 text-gray-600 cursor-not-allowed'
                          : 'border-gray-200 text-gray-400 cursor-not-allowed'
                        : darkMode
                          ? 'border-gray-600 text-gray-300 hover:bg-gray-800'
                          : 'border-gray-300 text-gray-700 hover:bg-gray-100'
                    }`}
                  >
                    Next
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Loading State */}
          {loading ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="flex flex-col items-center gap-2">
                <div className={`w-8 h-8 border-2 rounded-full animate-spin ${
                  darkMode
                    ? 'border-gray-600 border-t-gray-300'
                    : 'border-gray-300 border-t-gray-600'
                }`} />
                <div className={darkMode ? 'text-gray-400' : 'text-gray-500'}>Loading deals...</div>
              </div>
            </div>
          ) : deals.length === 0 ? (
            <div className="flex-1 flex items-center justify-center">
              <div className={darkMode ? 'text-gray-400' : 'text-gray-500'}>No deals found</div>
            </div>
          ) : (
            <DealTable
              deals={deals}
              onDealClick={handleDealClick}
              darkMode={darkMode}
            />
          )}
          </>
          )}
        </main>
      </div>

      {/* Deal Modal */}
      {selectedDeal && (
        <DealModal
          deal={selectedDeal}
          onClose={() => setSelectedDeal(null)}
          darkMode={darkMode}
        />
      )}
    </div>
  );
}

export default App;
