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
} from 'lucide-react';
import type { Deal, EnterpriseCategory, InvestmentStage, PaginatedDeals, SortDirection } from '../types';
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
          <button onClick={onExport} className="btn-primary flex items-center gap-1.5">
            <Download className="w-3 h-3" />
            EXPORT CSV
          </button>
          <button
            onClick={onRefresh}
            disabled={isLoading}
            className="btn-secondary flex items-center gap-1.5"
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
              <th className="px-6 py-3 border-b border-slate-800">Company / Source</th>
              <th className="px-6 py-3 border-b border-slate-800">Stage & Fund</th>
              <th className="px-6 py-3 border-b border-slate-800">AI Category</th>
              <th className="px-6 py-3 border-b border-slate-800 w-24 text-center">
                Links
              </th>
              <th
                className="px-6 py-3 border-b border-slate-800 text-right cursor-pointer hover:bg-slate-800/50 transition-colors select-none"
                onClick={() => onSortChange(sortDirection === 'desc' ? 'asc' : 'desc')}
              >
                <span className="flex items-center justify-end gap-1">
                  Announced
                  {sortDirection === 'desc' ? (
                    <ArrowDown className="w-3 h-3 text-emerald-400" />
                  ) : (
                    <ArrowUp className="w-3 h-3 text-emerald-400" />
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

function DealRow({ deal, onClick }: DealRowProps) {
  const isRejected = !deal.isEnterpriseAi || !deal.investorRoles.includes('lead');

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
        />
      </td>

      {/* Detected Time */}
      <td className="px-6 py-4 text-right text-slate-500 font-mono">
        {formatDate(deal.date)}
      </td>
    </tr>
  );
}

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
}: {
  website?: string;
  ceoLinkedin?: string;
  isRejected: boolean;
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
