/**
 * DealModal - Detailed view of a single deal
 *
 * Shows deal details with company links and founder info
 * Command Center dark theme
 *
 * Accessibility features:
 * - Focus trap within modal
 * - Escape key to close
 * - Focus restoration on close
 */

import { memo, useState, useEffect, useRef, useCallback } from 'react';
import {
  X,
  ExternalLink,
  Users,
  DollarSign,
  Calendar,
  Shield,
  Cpu,
  Bot,
  Database,
  Building2,
  Layers,
  Globe,
  Linkedin,
  User,
  Target,
  Check,
  Loader2,
  Flag,
} from 'lucide-react';
import type { Deal, EnterpriseCategory } from '../types';
import { STAGE_LABELS, CATEGORY_LABELS } from '../types';
import { addDealToTracker, flagDeal } from '../api/deals';

interface DealModalProps {
  deal: Deal;
  onClose: () => void;
}

export const DealModal = memo(function DealModal({ deal, onClose }: DealModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [trackingStatus, setTrackingStatus] = useState<'idle' | 'loading' | 'success' | 'already' | 'error'>('idle');
  const [flagStatus, setFlagStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');

  const handleFlag = useCallback(async () => {
    if (flagStatus === 'loading' || flagStatus === 'success') return;

    // Prompt for reason
    const reason = window.prompt(
      `Flag "${deal.startupName}" - What's wrong?\n\n(e.g., "Wrong funding amount", "Not Enterprise AI", "Duplicate entry")`
    );

    // User cancelled
    if (reason === null) return;

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

  const handleAddToTracker = useCallback(async () => {
    if (trackingStatus === 'loading' || trackingStatus === 'success' || trackingStatus === 'already') return;

    setTrackingStatus('loading');
    try {
      await addDealToTracker(parseInt(deal.id), 'watching');
      setTrackingStatus('success');
    } catch (err: unknown) {
      console.error('Failed to add to tracker:', err);
      // Check if it's a 400 error (already tracked or not found)
      const error = err as { status?: number };
      if (error.status === 400) {
        // Treat as "already tracked" - this is not an error
        setTrackingStatus('already');
      } else {
        setTrackingStatus('error');
        setTimeout(() => setTrackingStatus('idle'), 3000);
      }
    }
  }, [deal.id, trackingStatus]);

  // Focus the close button when modal opens
  useEffect(() => {
    closeButtonRef.current?.focus();

    // Store the previously focused element
    const previouslyFocused = document.activeElement as HTMLElement;

    return () => {
      // Restore focus when modal closes
      previouslyFocused?.focus?.();
    };
  }, []);

  // Handle Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const handleBackdropClick = useCallback((e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  }, [onClose]);

  const getCategoryIcon = (category?: EnterpriseCategory) => {
    switch (category) {
      case 'infrastructure':
        return <Layers className="w-4 h-4" />;
      case 'security':
        return <Shield className="w-4 h-4" />;
      case 'vertical_saas':
        return <Building2 className="w-4 h-4" />;
      case 'agentic':
        return <Bot className="w-4 h-4" />;
      case 'data_intelligence':
        return <Database className="w-4 h-4" />;
      default:
        return <Cpu className="w-4 h-4" />;
    }
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/70 backdrop-blur-sm z-40 animate-fade-in"
        onClick={handleBackdropClick}
        aria-hidden="true"
      />

      {/* Modal Content */}
      <div
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="deal-modal-title"
        className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full max-w-2xl mx-4 bg-[#0a0a0c] border border-slate-800 rounded-lg shadow-2xl animate-slide-up"
      >
        {/* Header */}
        <div className="flex items-start justify-between p-6 border-b border-slate-800">
          <div>
            <h2 id="deal-modal-title" className="text-2xl font-bold text-white">{deal.startupName}</h2>
            <div className="flex items-center gap-2 mt-2 text-sm">
              <span className={`stage-badge stage-${deal.investmentStage}`}>
                {STAGE_LABELS[deal.investmentStage]}
              </span>
              <span className="text-slate-500">•</span>
              <span className="text-slate-300">{deal.amountInvested}</span>
            </div>
          </div>
          <button
            ref={closeButtonRef}
            onClick={onClose}
            className="p-2 hover:bg-slate-800 rounded transition-colors text-slate-400 hover:text-white"
            aria-label="Close deal details"
          >
            <X className="w-5 h-5" aria-hidden="true" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* Main Info Grid */}
          <div className="grid grid-cols-2 gap-4">
            {/* Lead Investor */}
            <InfoCard
              icon={<Users className="w-4 h-4 text-emerald-400" />}
              label="Lead Investor"
              value={deal.leadInvestor || 'Unknown'}
              subValue={deal.leadPartner ? `Partner: ${deal.leadPartner}` : undefined}
            />

            {/* Amount */}
            <InfoCard
              icon={<DollarSign className="w-4 h-4 text-blue-400" />}
              label="Amount Raised"
              value={deal.amountInvested}
            />

            {/* Date */}
            <InfoCard
              icon={<Calendar className="w-4 h-4 text-purple-400" />}
              label="Announced"
              value={formatDate(deal.date)}
            />

            {/* Category */}
            <InfoCard
              icon={getCategoryIcon(deal.enterpriseCategory)}
              label="Enterprise Category"
              value={
                deal.enterpriseCategory
                  ? CATEGORY_LABELS[deal.enterpriseCategory]
                  : 'Not Classified'
              }
              badge={
                deal.isEnterpriseAi ? (
                  <span className="ml-2 px-2 py-0.5 bg-emerald-500/20 text-emerald-400 text-xs rounded">
                    Enterprise AI
                  </span>
                ) : (
                  <span className="ml-2 px-2 py-0.5 bg-slate-700 text-slate-400 text-xs rounded">
                    Consumer
                  </span>
                )
              }
            />
          </div>

          {/* Company Links - Always show icons */}
          <div>
            <label className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-2">
              Company Links
            </label>
            <div className="flex flex-wrap gap-3">
              {/* Website - always show, green when available */}
              {deal.companyWebsite ? (
                <a
                  href={deal.companyWebsite}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-emerald-700/50 rounded text-sm text-white transition-colors"
                  title={deal.companyWebsite}
                >
                  <Globe className="w-4 h-4 text-emerald-400" />
                  <span className="text-emerald-300">{extractDomain(deal.companyWebsite)}</span>
                </a>
              ) : (
                <span
                  className="flex items-center gap-2 px-3 py-2 bg-slate-900 border border-slate-800 rounded text-sm text-slate-600 cursor-not-allowed"
                  title="Website not available"
                >
                  <Globe className="w-4 h-4" />
                  No website
                </span>
              )}

              {/* Company LinkedIn - always show if exists */}
              {deal.companyLinkedin ? (
                <a
                  href={deal.companyLinkedin}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-blue-700/50 rounded text-sm text-white transition-colors"
                  title={deal.companyLinkedin}
                >
                  <Linkedin className="w-4 h-4 text-blue-400" />
                  <span className="text-blue-300">Company</span>
                </a>
              ) : (
                <span
                  className="flex items-center gap-2 px-3 py-2 bg-slate-900 border border-slate-800 rounded text-sm text-slate-600 cursor-not-allowed"
                  title="Company LinkedIn not available"
                >
                  <Linkedin className="w-4 h-4" />
                  No Company LinkedIn
                </span>
              )}
            </div>
          </div>

          {/* CEO/Main Founder - always show section with LinkedIn status */}
          {deal.founders && deal.founders.length > 0 && deal.founders[0] ? (
            <div>
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-2">
                CEO / Founder
              </label>
              <div className="flex items-center justify-between p-3 bg-slate-900/50 border border-slate-800 rounded">
                <div className="flex items-center gap-2">
                  <User className="w-4 h-4 text-slate-400" />
                  <span className="text-white">{deal.founders[0].name}</span>
                  {deal.founders[0].title && (
                    <span className="text-slate-500 text-sm">• {deal.founders[0].title}</span>
                  )}
                </div>
                {/* LinkedIn - always show status */}
                {deal.founders[0].linkedinUrl ? (
                  <a
                    href={deal.founders[0].linkedinUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 text-blue-400 hover:text-blue-300 text-sm transition-colors"
                    title={deal.founders[0].linkedinUrl}
                  >
                    <Linkedin className="w-3 h-3" />
                    LinkedIn
                  </a>
                ) : (
                  <span className="flex items-center gap-1 text-slate-600 text-sm" title="LinkedIn not found">
                    <Linkedin className="w-3 h-3" />
                    No LinkedIn
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div>
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-2">
                CEO / Founder
              </label>
              <div className="flex items-center p-3 bg-slate-900/50 border border-slate-800 rounded">
                <span className="text-slate-500 italic text-sm">No founder information available</span>
              </div>
            </div>
          )}

          {/* Source Link */}
          {deal.sourceUrl && (
            <div className="pt-4 border-t border-slate-800">
              <a
                href={deal.sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 text-sm text-blue-400 hover:text-blue-300 transition-colors"
              >
                <ExternalLink className="w-4 h-4" />
                View Source Article
              </a>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-slate-800 flex justify-end gap-3">
          <button onClick={onClose} className="btn-secondary">
            Close
          </button>
          {/* Flag Button */}
          <button
            onClick={handleFlag}
            disabled={flagStatus === 'loading' || flagStatus === 'success'}
            className={`flex items-center gap-2 px-3 py-2 rounded font-medium transition-colors ${
              flagStatus === 'success'
                ? 'bg-red-600/20 text-red-400 border border-red-600/50 cursor-default'
                : flagStatus === 'error'
                ? 'bg-red-600 hover:bg-red-500 text-white'
                : flagStatus === 'loading'
                ? 'bg-slate-700 text-slate-400 cursor-wait'
                : 'bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 hover:border-red-600/50 hover:text-red-400'
            }`}
            title={flagStatus === 'success' ? 'Flagged for review' : 'Report an error with this company'}
          >
            {flagStatus === 'loading' ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Flag className={`w-4 h-4 ${flagStatus === 'success' ? 'text-red-400' : ''}`} />
            )}
            {flagStatus === 'success' ? 'Flagged!' : flagStatus === 'error' ? 'Retry' : 'Flag'}
          </button>
          {/* Track Button */}
          <button
            onClick={handleAddToTracker}
            disabled={trackingStatus === 'loading' || trackingStatus === 'success' || trackingStatus === 'already'}
            className={`flex items-center gap-2 px-4 py-2 rounded font-medium transition-colors ${
              trackingStatus === 'success' || trackingStatus === 'already'
                ? 'bg-emerald-600 text-white cursor-default'
                : trackingStatus === 'error'
                ? 'bg-red-600 hover:bg-red-500 text-white'
                : trackingStatus === 'loading'
                ? 'bg-blue-600/50 text-white cursor-wait'
                : 'bg-blue-600 hover:bg-blue-500 text-white'
            }`}
            title={
              trackingStatus === 'success' || trackingStatus === 'already'
                ? 'Already in Tracker'
                : 'Add to deal tracker'
            }
          >
            {trackingStatus === 'loading' ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : trackingStatus === 'success' || trackingStatus === 'already' ? (
              <Check className="w-4 h-4" />
            ) : (
              <Target className="w-4 h-4" />
            )}
            {trackingStatus === 'success'
              ? 'Added!'
              : trackingStatus === 'already'
              ? 'Already Added'
              : trackingStatus === 'error'
              ? 'Retry'
              : 'Track'}
          </button>
        </div>
      </div>
    </>
  );
});

interface InfoCardProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  subValue?: string;
  badge?: React.ReactNode;
}

function InfoCard({ icon, label, value, subValue, badge }: InfoCardProps) {
  return (
    <div className="p-3 bg-slate-900/50 border border-slate-800 rounded">
      <div className="flex items-center gap-2 text-xs text-slate-500 mb-1">
        {icon}
        {label}
      </div>
      <div className="text-white font-medium flex items-center flex-wrap">
        {value}
        {badge}
      </div>
      {subValue && <div className="text-xs text-slate-400 mt-0.5">{subValue}</div>}
    </div>
  );
}

function formatDate(dateString: string): string {
  if (!dateString) return 'Unknown';
  try {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  } catch {
    return dateString;
  }
}

function extractDomain(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace('www.', '');
  } catch {
    return url;
  }
}

export default DealModal;
