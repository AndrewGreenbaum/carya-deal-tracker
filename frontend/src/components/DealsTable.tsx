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

import React, { memo, useState, useCallback } from 'react';
import {
  Link,
  FileText,
  Globe,
  Cpu,
  Shield,
  Building2,
  Bot,
  Database,
  XCircle,
  List,
  Download,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  ArrowUp,
  ArrowDown,
  Linkedin,
  Target,
  Check,
  Loader2,
  Flag,
} from 'lucide-react';
import type { Deal, EnterpriseCategory, InvestmentStage, PaginatedDeals, SortDirection } from '../types';
import { addDealToTracker, flagDeal } from '../api/deals';
import { STAGE_LABELS } from '../types';

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
}

export function DealsTable({
  deals,
  pagination,
  onDealClick,
  onExport,
  onRefresh,
  onPageChange,
  isLoading = false,
  sortDirection,
  onSortChange,
}: DealsTableProps) {
  const currentPage = Math.floor(pagination.offset / pagination.limit) + 1;
  const totalPages = Math.ceil(pagination.total / pagination.limit);

  return (
    <>
      {/* Header */}
      <div className="px-6 py-3 border-b border-slate-800 bg-[#0a0a0c] flex justify-between items-center">
        <h2 className="text-sm font-bold text-white uppercase tracking-widest flex items-center gap-2">
          <List className="w-4 h-4" /> Latest Extractions
          <span className="text-slate-500 font-normal">({pagination.total} total)</span>
        </h2>
        <div className="flex gap-2">
          <button
            onClick={onExport}
            className="btn-primary flex items-center gap-1.5"
            aria-label="Export deals to CSV"
          >
            <Download className="w-3 h-3" />
            EXPORT CSV
          </button>
          <button
            onClick={onRefresh}
            disabled={isLoading}
            className="btn-secondary flex items-center gap-1.5"
            aria-label={isLoading ? 'Refreshing deals...' : 'Refresh deals'}
          >
            <RefreshCw className={`w-3 h-3 ${isLoading ? 'animate-spin' : ''}`} />
            REFRESH
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-left border-collapse">
          <thead className="bg-[#0a0a0c] sticky top-0 z-10 text-[10px] uppercase font-bold text-slate-500">
            <tr>
              <th scope="col" className="px-6 py-3 border-b border-slate-800">Company / Source</th>
              <th scope="col" className="px-6 py-3 border-b border-slate-800">Stage & Fund</th>
              <th scope="col" className="px-6 py-3 border-b border-slate-800">AI Category</th>
              <th scope="col" className="px-6 py-3 border-b border-slate-800 w-24 text-center">
                Links
              </th>
              <th
                scope="col"
                aria-sort={sortDirection === 'desc' ? 'descending' : 'ascending'}
                className="px-6 py-3 border-b border-slate-800 text-right cursor-pointer hover:bg-slate-800/50 transition-colors select-none"
                onClick={() => onSortChange(sortDirection === 'desc' ? 'asc' : 'desc')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSortChange(sortDirection === 'desc' ? 'asc' : 'desc');
                  }
                }}
                tabIndex={0}
                role="button"
              >
                <span className="flex items-center justify-end gap-1">
                  Announced
                  {sortDirection === 'desc' ? (
                    <ArrowDown className="w-3 h-3 text-emerald-400" aria-hidden="true" />
                  ) : (
                    <ArrowUp className="w-3 h-3 text-emerald-400" aria-hidden="true" />
                  )}
                </span>
              </th>
            </tr>
          </thead>
          <tbody className="text-xs divide-y divide-slate-800/50">
            {deals.map((deal) => (
              <DealRow key={deal.id} deal={deal} onClick={() => onDealClick(deal)} />
            ))}
            {deals.length === 0 && (
              <tr>
                <td colSpan={5} className="px-6 py-12 text-center text-slate-500">
                  {isLoading ? 'Loading deals...' : 'No deals found matching your filters'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="px-6 py-3 border-t border-slate-800 bg-[#0a0a0c] flex justify-between items-center">
          <div className="text-xs text-slate-500">
            Showing {pagination.offset + 1}-
            {Math.min(pagination.offset + pagination.limit, pagination.total)} of{' '}
            {pagination.total}
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => onPageChange(Math.max(0, pagination.offset - pagination.limit))}
              disabled={pagination.offset === 0}
              className="btn-secondary flex items-center gap-1 disabled:opacity-50"
            >
              <ChevronLeft className="w-3 h-3" />
              Previous
            </button>
            <span className="px-3 py-1.5 text-xs text-slate-400">
              Page {currentPage} of {totalPages}
            </span>
            <button
              onClick={() => onPageChange(pagination.offset + pagination.limit)}
              disabled={!pagination.hasMore}
              className="btn-secondary flex items-center gap-1 disabled:opacity-50"
            >
              Next
              <ChevronRight className="w-3 h-3" />
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
}

const DealRow = memo(function DealRow({ deal, onClick }: DealRowProps) {
  const isRejected = !deal.isEnterpriseAi || !deal.investorRoles.includes('lead');
  const [trackingStatus, setTrackingStatus] = useState<'idle' | 'loading' | 'success' | 'already' | 'error'>('idle');
  const [flagStatus, setFlagStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');

  const handleTrack = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (trackingStatus === 'loading' || trackingStatus === 'success' || trackingStatus === 'already') return;

    setTrackingStatus('loading');
    try {
      await addDealToTracker(parseInt(deal.id), 'watching');
      setTrackingStatus('success');
      // Reset after 3 seconds
      setTimeout(() => setTrackingStatus('idle'), 3000);
    } catch (err: unknown) {
      console.error('Failed to add to tracker:', err);
      // Check if it's a 400 error (already tracked)
      const error = err as { status?: number };
      if (error.status === 400) {
        setTrackingStatus('already');
        // Keep showing "already" for 3s then reset
        setTimeout(() => setTrackingStatus('idle'), 3000);
      } else {
        setTrackingStatus('error');
        setTimeout(() => setTrackingStatus('idle'), 3000);
      }
    }
  }, [deal.id, trackingStatus]);

  const handleFlag = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (flagStatus === 'loading' || flagStatus === 'success') return;

    // Prompt for reason
    const reason = window.prompt(
      `Flag "${deal.startupName}" - What's wrong?\n\n(e.g., "Wrong amount", "Not Enterprise AI", "Duplicate")`
    );
    if (reason === null) return; // User cancelled

    setFlagStatus('loading');
    try {
      await flagDeal(
        deal.id ? parseInt(deal.id) : null,
        deal.startupName,
        reason || undefined,
        deal.sourceUrl || undefined
      );
      setFlagStatus('success');
    } catch (err) {
      console.error('Failed to flag deal:', err);
      setFlagStatus('error');
      setTimeout(() => setFlagStatus('idle'), 3000);
    }
  }, [deal.id, deal.startupName, deal.sourceUrl, flagStatus]);

  return (
    <tr
      onClick={onClick}
      className={`deal-row cursor-pointer group ${
        isRejected ? 'opacity-50 grayscale hover:grayscale-0 hover:opacity-100' : ''
      }`}
    >
      {/* Company / Source */}
      <td className="px-6 py-4">
        <div
          className={`text-sm font-bold group-hover:underline underline-offset-4 cursor-pointer ${
            isRejected ? 'text-slate-400 group-hover:text-white' : 'text-emerald-400'
          }`}
        >
          {deal.startupName}
        </div>
        <div className="text-[10px] text-slate-500 mt-0.5 flex items-center gap-1">
          <SourceIcon source={deal.sourceName} />
          {deal.sourceName || 'Unknown Source'}
        </div>
      </td>

      {/* Stage & Fund */}
      <td className="px-6 py-4">
        <div className="flex items-center gap-2 mb-1">
          <StageBadge stage={deal.investmentStage} isRejected={isRejected} />
        </div>
        <div className={`font-bold ${isRejected ? 'text-slate-400' : 'text-white'}`}>
          {deal.leadInvestor || 'Unknown'}
        </div>
      </td>

      {/* AI Category */}
      <td className="px-6 py-4">
        <CategoryBadge category={deal.enterpriseCategory} isRejected={isRejected} />
      </td>

      {/* Company Links */}
      <td className="px-6 py-4">
        <CompanyLinks
          website={deal.companyWebsite}
          ceoLinkedin={deal.founders?.[0]?.linkedinUrl}
          isRejected={isRejected}
          trackingStatus={trackingStatus}
          onTrack={handleTrack}
          flagStatus={flagStatus}
          onFlag={handleFlag}
        />
      </td>

      {/* Detected Time */}
      <td className="px-6 py-4 text-right text-slate-500 font-mono">
        {formatDate(deal.date)}
      </td>
    </tr>
  );
});

function SourceIcon({ source }: { source?: string }) {
  if (!source) return <Link className="w-3 h-3" />;

  const lowerSource = source.toLowerCase();
  if (lowerSource.includes('sec') || lowerSource.includes('form d')) {
    return <FileText className="w-3 h-3" />;
  }
  if (lowerSource.includes('blog') || lowerSource.includes('sequoia') || lowerSource.includes('a16z')) {
    return <Globe className="w-3 h-3" />;
  }
  return <Link className="w-3 h-3" />;
}

function StageBadge({ stage, isRejected }: { stage: InvestmentStage; isRejected: boolean }) {
  if (isRejected) {
    return (
      <span className="stage-badge bg-slate-800 text-slate-400 border-slate-700">REJECTED</span>
    );
  }

  return <span className={`stage-badge stage-${stage}`}>{STAGE_LABELS[stage]?.toUpperCase() || stage.toUpperCase()}</span>;
}

const CATEGORY_ICONS: Record<EnterpriseCategory, React.ReactNode> = {
  infrastructure: <Cpu className="w-3 h-3 text-slate-400" />,
  security: <Shield className="w-3 h-3 text-slate-400" />,
  vertical_saas: <Building2 className="w-3 h-3 text-slate-400" />,
  agentic: <Bot className="w-3 h-3 text-slate-400" />,
  data_intelligence: <Database className="w-3 h-3 text-slate-400" />,
  not_enterprise: <XCircle className="w-3 h-3 text-red-900" />,
};

function CategoryBadge({
  category,
  isRejected,
}: {
  category?: EnterpriseCategory;
  isRejected: boolean;
}) {
  const cat = category || 'not_enterprise';
  const icon = CATEGORY_ICONS[cat];

  if (isRejected || cat === 'not_enterprise') {
    return (
      <div className="category-badge category-not_enterprise">
        {icon}
        {category || 'consumer'}
      </div>
    );
  }

  return (
    <div className={`category-badge category-${cat}`}>
      {icon}
      {cat.replace('_', ' ')}
    </div>
  );
}

function CompanyLinks({
  website,
  ceoLinkedin,
  isRejected,
  trackingStatus,
  onTrack,
  flagStatus,
  onFlag,
}: {
  website?: string;
  ceoLinkedin?: string;
  isRejected: boolean;
  trackingStatus: 'idle' | 'loading' | 'success' | 'already' | 'error';
  onTrack: (e: React.MouseEvent) => void;
  flagStatus: 'idle' | 'loading' | 'success' | 'error';
  onFlag: (e: React.MouseEvent) => void;
}) {
  const hasWebsite = !!website;
  const hasLinkedin = !!ceoLinkedin;

  const handleClick = (e: React.MouseEvent, url: string) => {
    e.stopPropagation(); // Prevent row click
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  return (
    <div className="flex items-center justify-center gap-3">
      {/* Website Icon */}
      <button
        onClick={(e) => hasWebsite && handleClick(e, website!)}
        disabled={!hasWebsite}
        className={`p-1.5 rounded transition-all ${
          hasWebsite
            ? 'hover:bg-slate-700 text-emerald-400 hover:text-emerald-300 cursor-pointer'
            : 'text-slate-700 cursor-not-allowed'
        } ${isRejected ? 'opacity-50' : ''}`}
        title={hasWebsite ? `Visit ${website}` : 'No website'}
      >
        <Globe className="w-4 h-4" />
      </button>

      {/* LinkedIn Icon */}
      <button
        onClick={(e) => hasLinkedin && handleClick(e, ceoLinkedin!)}
        disabled={!hasLinkedin}
        className={`p-1.5 rounded transition-all ${
          hasLinkedin
            ? 'hover:bg-slate-700 text-blue-400 hover:text-blue-300 cursor-pointer'
            : 'text-slate-700 cursor-not-allowed'
        } ${isRejected ? 'opacity-50' : ''}`}
        title={hasLinkedin ? 'CEO LinkedIn' : 'No LinkedIn'}
      >
        <Linkedin className="w-4 h-4" />
      </button>

      {/* Track Button */}
      <button
        onClick={onTrack}
        disabled={trackingStatus === 'loading'}
        className={`p-1.5 rounded transition-all ${
          trackingStatus === 'success' || trackingStatus === 'already'
            ? 'text-emerald-400 bg-emerald-400/10'
            : trackingStatus === 'error'
            ? 'text-red-400 bg-red-400/10'
            : trackingStatus === 'loading'
            ? 'text-slate-400 cursor-wait'
            : 'hover:bg-slate-700 text-slate-400 hover:text-blue-400 cursor-pointer'
        } ${isRejected ? 'opacity-50' : ''}`}
        title={
          trackingStatus === 'success'
            ? 'Added to Tracker!'
            : trackingStatus === 'already'
            ? 'Already Added'
            : trackingStatus === 'error'
            ? 'Failed to add'
            : 'Add to Tracker'
        }
      >
        {trackingStatus === 'loading' ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : trackingStatus === 'success' || trackingStatus === 'already' ? (
          <Check className="w-4 h-4" />
        ) : (
          <Target className="w-4 h-4" />
        )}
      </button>

      {/* Flag Button */}
      <button
        onClick={onFlag}
        disabled={flagStatus === 'loading' || flagStatus === 'success'}
        className={`p-1.5 rounded transition-all ${
          flagStatus === 'success'
            ? 'text-red-400 bg-red-400/10'
            : flagStatus === 'error'
            ? 'text-red-500 bg-red-500/10'
            : flagStatus === 'loading'
            ? 'text-slate-400 cursor-wait'
            : 'hover:bg-slate-700 text-slate-400 hover:text-red-400 cursor-pointer'
        } ${isRejected ? 'opacity-50' : ''}`}
        title={
          flagStatus === 'success'
            ? 'Flagged for review'
            : flagStatus === 'error'
            ? 'Failed to flag'
            : 'Report an error'
        }
      >
        {flagStatus === 'loading' ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Flag className="w-4 h-4" />
        )}
      </button>
    </div>
  );
}

function formatDate(dateString: string): string {
  if (!dateString) return 'Unknown';

  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));

  if (diffHours < 24) {
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
  }

  if (diffHours < 48) {
    return 'Yesterday';
  }

  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export default DealsTable;
