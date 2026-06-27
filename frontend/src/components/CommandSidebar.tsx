/**
 * CommandSidebar - Minimal left panel with navigation, filters, and funds
 */

import { useEffect } from 'react';
import { X, Search } from 'lucide-react';
import type { FilterState, Fund, ViewType } from '../types';
import { TRACKED_FUNDS } from '../types';

interface CommandSidebarProps {
  filters: FilterState;
  onFiltersChange: (filters: FilterState) => void;
  funds: Fund[];
  currentView: ViewType;
  onViewChange: (view: ViewType) => void;
  onOpenFeedback?: () => void;
  onOpenAircraft?: () => void;
  isMobileOpen?: boolean;
  onMobileClose?: () => void;
}

export function CommandSidebar({
  filters,
  onFiltersChange,
  funds,
  currentView,
  onViewChange,
  onOpenFeedback: _onOpenFeedback,
  onOpenAircraft: _onOpenAircraft,
  isMobileOpen = false,
  onMobileClose,
}: CommandSidebarProps) {
  void _onOpenFeedback;
  void _onOpenAircraft;

  // Close sidebar when navigating on mobile
  const handleViewChange = (view: ViewType) => {
    onViewChange(view);
    onMobileClose?.();
  };

  // Prevent body scroll when mobile sidebar is open
  useEffect(() => {
    if (isMobileOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isMobileOpen]);

  const toggleFilter = (key: keyof FilterState) => {
    if (key === 'selectedFund' || key === 'selectedCategory' || key === 'searchQuery' || key === 'sortDirection') return;

    if (key === 'aiDealsOnly' && filters.aiDealsOnly) {
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

  const setFund = (fundSlug: string | undefined) => {
    onFiltersChange({
      ...filters,
      selectedFund: fundSlug,
    });
  };

  const displayFunds = TRACKED_FUNDS.map((tf) => {
    const apiFund = funds.find((f) => f.slug === tf.slug);
    return {
      ...tf,
      lastScraped: apiFund?.lastScraped,
      isActive: apiFund?.isActive ?? true,
    };
  });

  return (
    <>
      {/* Mobile overlay backdrop */}
      {isMobileOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-40 md:hidden"
          onClick={onMobileClose}
          aria-hidden="true"
        />
      )}

      <aside
        className={`
          w-64 h-full bg-black flex flex-col shrink-0
          md:relative md:translate-x-0
          fixed top-0 left-0 z-50 transition-transform duration-300 ease-in-out
          ${isMobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}
        `}
      >
        {/* Mobile close button */}
        <div className="md:hidden flex items-center justify-between px-6 py-3">
          <span className="text-sm font-medium text-slate-300">Menu</span>
          <button
            onClick={onMobileClose}
            className="w-10 h-10 flex items-center justify-center text-slate-400 hover:text-slate-200 rounded transition-colors"
            aria-label="Close menu"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Scrollable content */}
        <div className="flex-1 min-h-0 overflow-y-auto scrollbar-hide px-6">
          {/* Brand */}
          <div className="flex items-center gap-3 pt-6 pb-10">
            <div className="w-2.5 h-2.5 bg-blue-500 rounded-full" />
            <span className="text-lg font-semibold text-white tracking-tight">
              Eagle Eye
            </span>
          </div>

          {/* Navigation */}
          <nav className="space-y-1 pb-10">
            <NavItem
              label="Dashboard"
              active={currentView === 'dashboard'}
              onClick={() => handleViewChange('dashboard')}
            />
            <NavItem
              label="Tracker"
              active={currentView === 'tracker'}
              onClick={() => handleViewChange('tracker')}
            />
            <NavItem
              label="Settings"
              active={currentView === 'settings'}
              onClick={() => handleViewChange('settings')}
            />
          </nav>

          {/* Search */}
          <div className="pb-8">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-600" />
              <input
                type="text"
                placeholder="Search..."
                value={filters.searchQuery || ''}
                onChange={(e) => onFiltersChange({ ...filters, searchQuery: e.target.value })}
                className="w-full bg-transparent border border-slate-800 rounded-md pl-8 pr-8 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-slate-600"
              />
              {filters.searchQuery && (
                <button
                  onClick={() => onFiltersChange({ ...filters, searchQuery: '' })}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-600 hover:text-slate-300"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>

          {/* Filters */}
          <div className="pb-10">
            <div className="text-xs uppercase text-blue-500 font-medium mb-4 tracking-wider">
              Filters
            </div>
            <div className="space-y-4">
              <FilterCheckbox
                label="Enterprise AI"
                checked={filters.enterpriseAiOnly}
                onChange={() => {
                  // If enterprise AI is being turned on, also turn on aiDealsOnly
                  if (!filters.enterpriseAiOnly) {
                    onFiltersChange({ ...filters, aiDealsOnly: true, enterpriseAiOnly: true });
                  } else {
                    onFiltersChange({ ...filters, enterpriseAiOnly: false });
                  }
                }}
              />
              <FilterCheckbox
                label="Lead Investors"
                checked={filters.leadOnly}
                onChange={() => toggleFilter('leadOnly')}
              />
            </div>
          </div>

          {/* Funds */}
          <div className="pb-6">
            <div className="flex items-center justify-between mb-4">
              <div className="text-xs uppercase text-blue-500 font-medium tracking-wider">
                Funds
              </div>
              {filters.selectedFund && (
                <button
                  onClick={() => setFund(undefined)}
                  className="text-[10px] text-blue-400 hover:text-blue-300"
                >
                  Clear
                </button>
              )}
            </div>
            <ul className="space-y-1 text-sm" role="listbox" aria-label="Tracked funds filter">
              {displayFunds.map((fund) => (
                <li key={fund.slug} role="presentation">
                  <button
                    type="button"
                    role="option"
                    aria-selected={fund.slug === filters.selectedFund}
                    onClick={() => setFund(fund.slug === filters.selectedFund ? undefined : fund.slug)}
                    className={`w-full text-left px-2 py-1.5 rounded text-sm transition-colors cursor-pointer ${
                      fund.slug === filters.selectedFund
                        ? 'text-blue-400'
                        : 'text-slate-500 hover:text-slate-300'
                    } ${!fund.isActive ? 'opacity-50' : ''}`}
                  >
                    {fund.name}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </aside>
    </>
  );
}

interface NavItemProps {
  label: string;
  active?: boolean;
  onClick: () => void;
}

function NavItem({ label, active, onClick }: NavItemProps) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-2 py-2 rounded text-base transition-colors min-h-[44px] md:min-h-0 ${
        active
          ? 'text-white font-medium'
          : 'text-slate-500 hover:text-slate-300'
      }`}
    >
      {label}
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
    <label className="flex items-center gap-3 text-sm cursor-pointer group min-h-[44px] md:min-h-0">
      <button
        onClick={onChange}
        className={`w-5 h-5 rounded flex items-center justify-center transition-colors ${
          checked
            ? 'bg-blue-600 text-white'
            : 'border border-slate-700 text-transparent hover:border-slate-500'
        }`}
      >
        {checked && (
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M2.5 6L5 8.5L9.5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        )}
      </button>
      <span className={checked ? 'text-white' : 'text-slate-400'}>{label}</span>
    </label>
  );
}

export default CommandSidebar;
