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
  AlertTriangle,
  Pencil,
} from 'lucide-react';
import type { Deal, EnterpriseCategory } from '../types';
import { STAGE_LABELS, CATEGORY_LABELS } from '../types';
import { addDealToTracker, flagDeal } from '../api/deals';
import { DealEditModal } from './DealEditModal';

interface DealModalProps {
  deal: Deal;
  onClose: () => void;
  onDealUpdated?: (updatedDeal: Deal) => void;
}

export const DealModal = memo(function DealModal({ deal, onClose, onDealUpdated }: DealModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [trackingStatus, setTrackingStatus] = useState<'idle' | 'loading' | 'success' | 'already' | 'error'>('idle');
  const [flagStatus, setFlagStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [currentDeal, setCurrentDeal] = useState(deal);

  // Update currentDeal when deal prop changes
  useEffect(() => {
    setCurrentDeal(deal);
  }, [deal]);

  const handleDealSaved = useCallback((updatedDeal: Deal) => {
    setCurrentDeal(updatedDeal);
    onDealUpdated?.(updatedDeal);
  }, [onDealUpdated]);

  const handleFlag = useCallback(async () => {
    if (flagStatus === 'loading' || flagStatus === 'success') return;

    // Prompt for reason
    const reason = window.prompt(
      `Flag "${currentDeal.startupName}" - What's wrong?\n\n(e.g., "Wrong funding amount", "Not Enterprise AI", "Duplicate entry")`
    );

    // User cancelled
    if (reason === null) return;

    setFlagStatus('loading');
    try {
      await flagDeal(
        currentDeal.id ? parseInt(currentDeal.id) : null,
        currentDeal.startupName,
        reason || undefined,
        currentDeal.sourceUrl || undefined
      );
      setFlagStatus('success');
    } catch (err) {
      console.error('Failed to flag deal:', err);
      setFlagStatus('error');
      setTimeout(() => setFlagStatus('idle'), 3000);
    }
  }, [currentDeal.id, currentDeal.startupName, currentDeal.sourceUrl, flagStatus]);

  const handleAddToTracker = useCallback(async () => {
    if (trackingStatus === 'loading' || trackingStatus === 'success' || trackingStatus === 'already') return;

    setTrackingStatus('loading');
    try {
      await addDealToTracker(parseInt(currentDeal.id), 'watching');
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
  }, [currentDeal.id, trackingStatus]);

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
            <h2 id="deal-modal-title" className="text-2xl font-bold text-white">{currentDeal.startupName}</h2>
            <div className="flex items-center gap-2 mt-2 text-sm">
              <span className={`stage-badge stage-${currentDeal.investmentStage}`}>
                {STAGE_LABELS[currentDeal.investmentStage]}
              </span>
              <span className="text-slate-500">•</span>
              <span className="text-slate-300">{currentDeal.amountInvested}</span>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {/* Edit Button - tiny icon */}
            <button
              onClick={() => setIsEditModalOpen(true)}
              className="p-1.5 hover:bg-slate-800 rounded transition-colors text-slate-500 hover:text-blue-400"
              aria-label="Edit deal"
              title="Edit"
            >
              <Pencil className="w-3.5 h-3.5" aria-hidden="true" />
            </button>
            <button
              ref={closeButtonRef}
              onClick={onClose}
              className="p-2 hover:bg-slate-800 rounded transition-colors text-slate-400 hover:text-white"
              aria-label="Close deal details"
            >
              <X className="w-5 h-5" aria-hidden="true" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* Main Info Grid */}
          <div className="grid grid-cols-2 gap-4">
            {/* Lead Investor */}
            <InfoCard
              icon={<Users className="w-4 h-4 text-emerald-400" />}
              label="Lead Investor"
              value={currentDeal.leadInvestor || 'Unknown'}
              subValue={currentDeal.leadPartner ? `Partner: ${currentDeal.leadPartner}` : undefined}
            />

            {/* Amount */}
            <InfoCard
              icon={<DollarSign className="w-4 h-4 text-blue-400" />}
              label="Amount Raised"
              value={currentDeal.amountInvested}
            />

            {/* Date */}
            <InfoCard
              icon={<Calendar className="w-4 h-4 text-purple-400" />}
              label="Announced"
              value={formatDate(currentDeal.date)}
            />

            {/* Category */}
            <InfoCard
              icon={getCategoryIcon(currentDeal.enterpriseCategory)}
              label="Enterprise Category"
              value={
                currentDeal.enterpriseCategory
                  ? CATEGORY_LABELS[currentDeal.enterpriseCategory]
                  : 'Not Classified'
              }
              badge={
                currentDeal.isEnterpriseAi ? (
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
              {currentDeal.companyWebsite ? (
                <a
                  href={currentDeal.companyWebsite}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-emerald-700/50 rounded text-sm text-white transition-colors"
                  title={currentDeal.companyWebsite}
                >
                  <Globe className="w-4 h-4 text-emerald-400" />
                  <span className="text-emerald-300">{extractDomain(currentDeal.companyWebsite)}</span>
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
              {currentDeal.companyLinkedin ? (
                <a
                  href={currentDeal.companyLinkedin}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-blue-700/50 rounded text-sm text-white transition-colors"
                  title={currentDeal.companyLinkedin}
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

          {/* Founders - show all founders with LinkedIn status */}
          {currentDeal.founders && currentDeal.founders.length > 0 ? (
            <div>
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-2">
                {currentDeal.founders.length === 1 ? 'Founder' : `Founders (${currentDeal.founders.length})`}
              </label>
              <div className="space-y-2">
                {currentDeal.founders.map((founder, index) => (
                  <div key={index} className="flex items-center justify-between p-3 bg-slate-900/50 border border-slate-800 rounded">
                    <div className="flex items-center gap-2">
                      <User className="w-4 h-4 text-slate-400" />
                      <span className="text-white">{founder.name}</span>
                      {founder.title && (
                        <span className="text-slate-500 text-sm">• {founder.title}</span>
                      )}
                    </div>
                    {/* LinkedIn - always show status */}
                    {founder.linkedinUrl ? (
                      <a
                        href={founder.linkedinUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 text-blue-400 hover:text-blue-300 text-sm transition-colors"
                        title={founder.linkedinUrl}
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
                ))}
              </div>
            </div>
          ) : (
            <div>
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-2">
                Founders
              </label>
              <div className="flex items-center p-3 bg-slate-900/50 border border-slate-800 rounded">
                <span className="text-slate-500 italic text-sm">No founder information available</span>
              </div>
            </div>
          )}

          {/* Verification Snippet - proof of lead status */}
          {currentDeal.verificationSnippet && (
            <div>
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-2 flex items-center gap-2">
                Lead Verification
                {currentDeal.leadEvidenceWeak && (
                  <span className="flex items-center gap-1 text-amber-400 font-normal normal-case tracking-normal">
                    <AlertTriangle className="w-3 h-3" />
                    Weak Evidence
                  </span>
                )}
              </label>
              <div className={`p-3 rounded ${
                currentDeal.leadEvidenceWeak
                  ? 'bg-amber-900/20 border border-amber-700/30'
                  : 'bg-emerald-900/20 border border-emerald-700/30'
              }`}>
                <p className={`text-sm italic ${
                  currentDeal.leadEvidenceWeak ? 'text-amber-300' : 'text-emerald-300'
                }`}>"{currentDeal.verificationSnippet}"</p>
                {currentDeal.leadEvidenceWeak && (
                  <p className="text-xs text-amber-400/70 mt-2">
                    ⚠ Snippet lacks explicit "led by" language — Claude inferred lead status
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Source Link */}
          {currentDeal.sourceUrl && (
            <div className="pt-4 border-t border-slate-800">
              <a
                href={currentDeal.sourceUrl}
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
                ? 'Already tracked'
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
              ? 'Tracked'
              : trackingStatus === 'already'
              ? 'Tracked'
              : trackingStatus === 'error'
              ? 'Retry'
              : 'Track'}
          </button>
        </div>
      </div>

      {/* Edit Modal */}
      {isEditModalOpen && (
        <DealEditModal
          deal={currentDeal}
          onClose={() => setIsEditModalOpen(false)}
          onSave={handleDealSaved}
        />
      )}
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
