/**
 * CommandSidebar - Left panel with filters, fund list, and cost tracker
 *
 * Features:
 * - Filter toggles (Enterprise AI, Lead Only, Show Rejected)
 * - Tracked funds list with last scraped times
 * - Category dropdown filter
 * - Cost tracker widget
 */

import { useState } from 'react';
import { Home, ChevronDown, ChevronUp, Search, X, Kanban, Mail } from 'lucide-react';
import type { FilterState, EnterpriseCategory, Fund, ViewType, CostTracker } from '../types';
import { CATEGORY_LABELS, TRACKED_FUNDS } from '../types';

interface CommandSidebarProps {
  filters: FilterState;
  onFiltersChange: (filters: FilterState) => void;
  funds: Fund[];
  currentView: ViewType;
  onViewChange: (view: ViewType) => void;
  costTracker?: CostTracker;
  onOpenFeedback?: () => void;
}

export function CommandSidebar({
  filters,
  onFiltersChange,
  funds,
  currentView,
  onViewChange,
  costTracker,
  onOpenFeedback,
}: CommandSidebarProps) {
  const [showCategoryDropdown, setShowCategoryDropdown] = useState(false);

  const toggleFilter = (key: keyof FilterState) => {
    if (key === 'selectedFund' || key === 'selectedCategory' || key === 'searchQuery' || key === 'sortDirection') return;

    // Handle mutual exclusivity between aiDealsOnly and enterpriseAiOnly
    if (key === 'enterpriseAiOnly' && !filters.enterpriseAiOnly) {
      // Turning on Enterprise AI Only implies All AI
      onFiltersChange({
        ...filters,
        aiDealsOnly: true,
        enterpriseAiOnly: true,
      });
    } else if (key === 'aiDealsOnly' && filters.aiDealsOnly) {
      // Turning off All AI also turns off Enterprise AI Only
      onFiltersChange({
        ...filters,
        aiDealsOnly: false,
        enterpriseAiOnly: false,
      });
    } else {
      onFiltersChange({
        ...filters,
        [key]: !filters[key],
      });
    }
  };

  const setCategory = (category: EnterpriseCategory | undefined) => {
    onFiltersChange({
      ...filters,
      selectedCategory: category,
    });
    setShowCategoryDropdown(false);
  };

  const setFund = (fundSlug: string | undefined) => {
    onFiltersChange({
      ...filters,
      selectedFund: fundSlug,
    });
  };

  // Merge tracked funds with any additional funds from API
  const displayFunds = TRACKED_FUNDS.map((tf) => {
    const apiFund = funds.find((f) => f.slug === tf.slug);
    return {
      ...tf,
      lastScraped: apiFund?.lastScraped,
      isActive: apiFund?.isActive ?? true,
    };
  });

  return (
    <aside className="w-64 bg-[#0d0d10] border-r border-slate-800 flex flex-col shrink-0">
      {/* Navigation */}
      <nav className="p-4 border-b border-slate-800">
        <div className="space-y-1">
          <NavItem
            icon={<Home className="w-4 h-4" />}
            label="Dashboard"
            active={currentView === 'dashboard'}
            onClick={() => onViewChange('dashboard')}
          />
          <NavItem
            icon={<Kanban className="w-4 h-4" />}
            label="Tracker"
            active={currentView === 'tracker'}
            onClick={() => onViewChange('tracker')}
          />
        </div>
      </nav>

      {/* Search */}
      <div className="p-4 border-b border-slate-800">
        <div className="text-[10px] uppercase text-slate-500 font-bold mb-2 tracking-widest">
          Search
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <input
            type="text"
            placeholder="Search companies..."
            value={filters.searchQuery || ''}
            onChange={(e) => onFiltersChange({ ...filters, searchQuery: e.target.value })}
            className="w-full bg-slate-900 border border-slate-800 rounded pl-8 pr-8 py-1.5 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-emerald-500/50"
          />
          {filters.searchQuery && (
            <button
              onClick={() => onFiltersChange({ ...filters, searchQuery: '' })}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="p-4 border-b border-slate-800">
        <div className="text-[10px] uppercase text-slate-500 font-bold mb-3 tracking-widest">
          Filters
        </div>
        <div className="space-y-2">
          <FilterCheckbox
            label="All AI Deals"
            checked={filters.aiDealsOnly}
            onChange={() => toggleFilter('aiDealsOnly')}
          />
          <FilterCheckbox
            label="Enterprise AI Only"
            checked={filters.enterpriseAiOnly}
            onChange={() => toggleFilter('enterpriseAiOnly')}
          />
          <FilterCheckbox
            label="Lead Investors Only"
            checked={filters.leadOnly}
            onChange={() => toggleFilter('leadOnly')}
          />
          <FilterCheckbox
            label="Show Rejected"
            checked={filters.showRejected}
            onChange={() => toggleFilter('showRejected')}
          />

          {/* Category Dropdown */}
          <div className="mt-3">
            <button
              onClick={() => setShowCategoryDropdown(!showCategoryDropdown)}
              className="w-full flex items-center justify-between text-sm text-slate-400 hover:text-white py-1.5 px-2 bg-slate-900 rounded border border-slate-800"
            >
              <span>
                {filters.selectedCategory
                  ? CATEGORY_LABELS[filters.selectedCategory]
                  : 'All Categories'}
              </span>
              {showCategoryDropdown ? (
                <ChevronUp className="w-4 h-4" />
              ) : (
                <ChevronDown className="w-4 h-4" />
              )}
            </button>

            {showCategoryDropdown && (
              <div className="mt-1 bg-slate-900 border border-slate-800 rounded overflow-hidden">
                <button
                  onClick={() => setCategory(undefined)}
                  className={`w-full text-left text-xs px-2 py-1.5 hover:bg-slate-800 ${
                    !filters.selectedCategory ? 'text-emerald-400' : 'text-slate-400'
                  }`}
                >
                  All Categories
                </button>
                {(Object.keys(CATEGORY_LABELS) as EnterpriseCategory[])
                  .filter((c) => c !== 'not_ai')
                  .map((category) => (
                    <button
                      key={category}
                      onClick={() => setCategory(category)}
                      className={`w-full text-left text-xs px-2 py-1.5 hover:bg-slate-800 ${
                        filters.selectedCategory === category
                          ? 'text-emerald-400'
                          : 'text-slate-400'
                      }`}
                    >
                      {CATEGORY_LABELS[category]}
                    </button>
                  ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Tracked Funds List */}
      <div className="p-4 flex-1 overflow-y-auto scrollbar-hide">
        <div className="flex items-center justify-between mb-3">
          <div className="text-[10px] uppercase text-slate-500 font-bold tracking-widest">
            Tracked Funds [{displayFunds.length}]
          </div>
          {filters.selectedFund && (
            <button
              onClick={() => setFund(undefined)}
              className="text-[10px] text-emerald-400 hover:underline"
            >
              Clear
            </button>
          )}
        </div>
        <ul className="space-y-1 text-xs text-slate-400" role="listbox" aria-label="Tracked funds filter">
          {displayFunds.map((fund) => (
            <li key={fund.slug} role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={fund.slug === filters.selectedFund}
                onClick={() => setFund(fund.slug === filters.selectedFund ? undefined : fund.slug)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setFund(fund.slug === filters.selectedFund ? undefined : fund.slug);
                  }
                }}
                className={`w-full flex items-center justify-between px-2 py-1.5 rounded text-left transition-colors cursor-pointer ${
                  fund.slug === filters.selectedFund
                    ? 'bg-emerald-500/10 text-emerald-400 border-l-2 border-emerald-500'
                    : 'hover:bg-slate-800 hover:text-white'
                } ${!fund.isActive ? 'opacity-50' : ''}`}
              >
                <span>{fund.name}</span>
                {fund.lastScraped && (
                  <span className="text-[9px] text-slate-600">
                    {formatTimeAgo(fund.lastScraped)}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>

        {/* Cost Tracker */}
        {costTracker && (
          <div className="mt-8 p-3 bg-slate-900 border border-slate-800 rounded text-center">
            <div className="text-[10px] text-slate-500 mb-1">CURRENT COST</div>
            <div className="text-xl font-bold text-white">
              ${costTracker.currentMonth.toFixed(0)}
              <span className="text-sm text-slate-500">/mo</span>
            </div>
            <div className="w-full bg-slate-800 h-1 mt-2 rounded-full overflow-hidden">
              <div
                className="bg-blue-500 h-full transition-all"
                style={{
                  width: `${Math.min(
                    (costTracker.currentMonth / costTracker.monthlyLimit) * 100,
                    100
                  )}%`,
                }}
              />
            </div>
            <div className="text-[9px] text-slate-500 mt-1 flex justify-between">
              <span>Used</span>
              <span>${costTracker.monthlyLimit} Limit</span>
            </div>
          </div>
        )}
      </div>

      {/* Feedback Section */}
      <div className="p-4 border-t border-slate-800 text-center">
        <button
          onClick={onOpenFeedback}
          className="p-2 hover:bg-slate-800 text-slate-400 hover:text-white rounded transition-colors"
          title="Submit feedback"
        >
          <Mail className="w-5 h-5" />
        </button>
      </div>
    </aside>
  );
}

interface NavItemProps {
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
}

function NavItem({ icon, label, active, disabled, onClick }: NavItemProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`w-full flex items-center gap-2 px-3 py-2 rounded text-sm transition-colors ${
        active
          ? 'bg-emerald-500/10 text-emerald-400 border-l-2 border-emerald-500'
          : disabled
          ? 'text-slate-600 cursor-not-allowed'
          : 'text-slate-400 hover:bg-slate-800 hover:text-white'
      }`}
    >
      {icon}
      <span>{label}</span>
      {disabled && (
        <span className="ml-auto text-[9px] text-slate-600 uppercase">Soon</span>
      )}
    </button>
  );
}

interface FilterCheckboxProps {
  label: string;
  checked: boolean;
  onChange: () => void;
}

function FilterCheckbox({ label, checked, onChange }: FilterCheckboxProps) {
  return (
    <label className="flex items-center gap-2 text-sm cursor-pointer hover:text-white group">
      <div
        className={`filter-checkbox ${checked ? 'checked' : 'unchecked'}`}
        onClick={onChange}
      >
        {checked && '✓'}
      </div>
      <span className="group-hover:translate-x-1 transition-transform">{label}</span>
    </label>
  );
}

function formatTimeAgo(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMinutes = Math.floor(diffMs / 60000);

  if (diffMinutes < 1) return 'just now';
  if (diffMinutes < 60) return `${diffMinutes}m ago`;

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

export default CommandSidebar;
