/**
 * CommandSidebar - Minimal navigation
 */

import { useEffect } from 'react';
import { X, Settings } from 'lucide-react';
import type { FilterState, Fund, ViewType } from '../types';

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
  filters: _filters,
  onFiltersChange: _onFiltersChange,
  funds: _funds,
  currentView,
  onViewChange,
  onOpenFeedback: _onOpenFeedback,
  onOpenAircraft: _onOpenAircraft,
  isMobileOpen = false,
  onMobileClose,
}: CommandSidebarProps) {
  // Keep props for API compatibility
  void _filters;
  void _onFiltersChange;
  void _funds;
  void _onOpenFeedback;
  void _onOpenAircraft;

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

      {/* Sidebar */}
      <aside
        className={`
          w-52 h-full bg-black border-r border-white/[0.06] flex flex-col shrink-0
          md:relative md:translate-x-0
          fixed top-0 left-0 z-50 transition-transform duration-300 ease-in-out
          ${isMobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}
        `}
      >
        {/* Mobile close button */}
        <div className="md:hidden flex items-center justify-end px-4 py-3 border-b border-white/[0.06]">
          <button
            onClick={onMobileClose}
            className="w-10 h-10 flex items-center justify-center text-white/40 hover:text-white/70 transition-colors"
            aria-label="Close menu"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-4 py-8">
          <div className="space-y-1">
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
              icon={<Settings className="w-4 h-4" />}
            />
          </div>
        </nav>
      </aside>
    </>
  );
}

interface NavItemProps {
  label: string;
  active?: boolean;
  onClick: () => void;
  icon?: React.ReactNode;
}

function NavItem({ label, active, onClick, icon }: NavItemProps) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 text-[15px] transition-colors ${
        active
          ? 'text-white'
          : 'text-white/40 hover:text-white/70'
      }`}
    >
      <span className="flex items-center gap-2">
        {icon}
        {label}
      </span>
    </button>
  );
}

export default CommandSidebar;
