/**
 * DealsTable - Main deals table with company links
 *
 * Features:
 * - Company name with source
 * - Stage badge & Lead fund
 * - Enterprise category with icon
 * - Company links (website + CEO LinkedIn icons)
 * - Detection time
 * - Click to open modal
 */

import { memo } from 'react';
import {
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import type { Deal, PaginatedDeals, SortDirection } from '../types';
import { STAGE_LABELS } from '../types';
import { DealCard } from './DealCard';

interface DealsTableProps {
  deals: Deal[];
  pagination: PaginatedDeals;
  onDealClick: (deal: Deal) => void;
  onExport: () => void;
  onRefresh: () => void;
  onPageChange: (offset: number) => void;
  isLoading?: boolean;
  sortDirection: SortDirection;
  onSortChange: (direction: SortDirection) => void;
  showRejected?: boolean;
}

export function DealsTable({
  deals,
  pagination,
  onDealClick,
  onExport: _onExport,
  onRefresh: _onRefresh,
  onPageChange,
  isLoading = false,
  sortDirection: _sortDirection,
  onSortChange: _onSortChange,
  showRejected = false,
}: DealsTableProps) {
  // Keeping props for API compatibility, but not using them in minimal UI
  void _onExport;
  void _onRefresh;
  void _sortDirection;
  void _onSortChange;
  const currentPage = Math.floor(pagination.offset / pagination.limit) + 1;
  const totalPages = Math.ceil(pagination.total / pagination.limit);

  return (
    <>
      {/* Mobile Card View */}
      <div className="md:hidden flex-1 overflow-auto scrollbar-hide p-4 space-y-3">
        {deals.map((deal) => (
          <DealCard
            key={deal.id}
            deal={deal}
            onClick={() => onDealClick(deal)}
            showRejected={showRejected}
          />
        ))}
        {deals.length === 0 && (
          <div className="py-12 text-center text-slate-500">
            {isLoading ? 'Loading deals...' : 'No deals found matching your filters'}
          </div>
        )}
      </div>

      {/* Desktop List View */}
      <div className="hidden md:block flex-1 overflow-auto scrollbar-hide">
        <div className="divide-y divide-slate-800/40">
          {deals.map((deal) => (
            <DealRow key={deal.id} deal={deal} onClick={() => onDealClick(deal)} showRejected={showRejected} />
          ))}
          {deals.length === 0 && (
            <div className="px-6 py-12 text-center text-slate-500">
              {isLoading ? 'Loading deals...' : 'No deals found matching your filters'}
            </div>
          )}
        </div>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="px-4 sm:px-6 py-3 border-t border-slate-800 bg-[#0a0a0c] flex flex-col sm:flex-row justify-between items-center gap-2">
          <div className="text-xs text-slate-500 order-2 sm:order-1">
            <span className="hidden sm:inline">Showing </span>
            {pagination.offset + 1}-{Math.min(pagination.offset + pagination.limit, pagination.total)}
            <span className="hidden sm:inline"> of</span>
            <span className="sm:hidden">/</span> {pagination.total}
          </div>
          <div className="flex gap-2 order-1 sm:order-2 w-full sm:w-auto justify-between sm:justify-end">
            <button
              onClick={() => onPageChange(Math.max(0, pagination.offset - pagination.limit))}
              disabled={pagination.offset === 0}
              className="btn-secondary flex items-center justify-center gap-1 disabled:opacity-50 min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0"
            >
              <ChevronLeft className="w-4 h-4 sm:w-3 sm:h-3" />
              <span className="hidden sm:inline">Previous</span>
            </button>
            <span className="px-3 py-1.5 text-xs text-slate-400 flex items-center">
              {currentPage}/{totalPages}
            </span>
            <button
              onClick={() => onPageChange(pagination.offset + pagination.limit)}
              disabled={!pagination.hasMore}
              className="btn-secondary flex items-center justify-center gap-1 disabled:opacity-50 min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0"
            >
              <span className="hidden sm:inline">Next</span>
              <ChevronRight className="w-4 h-4 sm:w-3 sm:h-3" />
            </button>
          </div>
        </div>
      )}
    </>
  );
}

interface DealRowProps {
  deal: Deal;
  onClick: () => void;
  showRejected?: boolean;
}

const DealRow = memo(function DealRow({ deal, onClick, showRejected = false }: DealRowProps) {
  const isRejected = !deal.investorRoles.includes('lead');
  const shouldDim = isRejected && !showRejected;

  const stageLabel = STAGE_LABELS[deal.investmentStage] || deal.investmentStage;
  const category = deal.enterpriseCategory?.replace('_', ' ');

  return (
    <div
      onClick={onClick}
      className={`px-6 py-5 cursor-pointer hover:bg-slate-900/30 transition-colors ${
        shouldDim ? 'opacity-50 hover:opacity-100' : ''
      }`}
    >
      <div className="text-base font-semibold text-white">
        {deal.startupName}
      </div>
      <div className="text-sm text-slate-500 mt-1 flex items-center gap-1.5">
        <span>{stageLabel}</span>
        <span className="text-slate-700">·</span>
        <span>{deal.leadInvestor || 'Unknown'}</span>
        {category && category !== 'not ai' && (
          <>
            <span className="text-slate-700">·</span>
            <span className="text-blue-400 capitalize">{category}</span>
          </>
        )}
      </div>
    </div>
  );
});

export default DealsTable;
