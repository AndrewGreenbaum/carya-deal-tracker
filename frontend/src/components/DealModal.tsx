/**
 * DealModal - Detailed view of a single deal
 *
 * Shows all deal fields with copy functionality for verification snippet
 * Command Center dark theme
 */

import { useState } from 'react';
import {
  X,
  Copy,
  Check,
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
} from 'lucide-react';
import type { Deal, EnterpriseCategory } from '../types';
import { STAGE_LABELS, CATEGORY_LABELS } from '../types';

interface DealModalProps {
  deal: Deal;
  onClose: () => void;
}

export function DealModal({ deal, onClose }: DealModalProps) {
  const [copied, setCopied] = useState(false);

  const copySnippet = async () => {
    if (deal.verificationSnippet) {
      await navigator.clipboard.writeText(deal.verificationSnippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

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
      />

      {/* Modal Content */}
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full max-w-2xl mx-4 bg-[#0a0a0c] border border-slate-800 rounded-lg shadow-2xl animate-slide-up">
        {/* Header */}
        <div className="flex items-start justify-between p-6 border-b border-slate-800">
          <div>
            <h2 className="text-2xl font-bold text-white">{deal.startupName}</h2>
            <div className="flex items-center gap-2 mt-2 text-sm">
              <span className={`stage-badge stage-${deal.investmentStage}`}>
                {STAGE_LABELS[deal.investmentStage]}
              </span>
              <span className="text-slate-500">•</span>
              <span className="text-slate-300">{deal.amountInvested}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-slate-800 rounded transition-colors text-slate-400 hover:text-white"
          >
            <X className="w-5 h-5" />
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

          {/* Verification Snippet */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                Verification Snippet
              </label>
              {deal.verificationSnippet && (
                <button
                  onClick={copySnippet}
                  className="flex items-center gap-1 text-xs text-slate-400 hover:text-white transition-colors"
                >
                  {copied ? (
                    <>
                      <Check className="w-3 h-3 text-emerald-400" />
                      <span className="text-emerald-400">Copied!</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" />
                      <span>Copy</span>
                    </>
                  )}
                </button>
              )}
            </div>
            <div className="p-4 bg-slate-900 border-l-2 border-emerald-500 rounded-r">
              {deal.verificationSnippet ? (
                <p className="text-slate-300 italic">"{deal.verificationSnippet}"</p>
              ) : (
                <p className="text-slate-500 italic">No verification snippet available</p>
              )}
            </div>
          </div>

          {/* Company Links */}
          <div>
            <label className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-2">
              Company Links
            </label>
            <div className="flex flex-wrap gap-3">
              {deal.companyWebsite && (
                <a
                  href={deal.companyWebsite}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded text-sm text-white transition-colors"
                >
                  <Globe className="w-4 h-4 text-blue-400" />
                  Website
                </a>
              )}
              {deal.companyLinkedin && (
                <a
                  href={deal.companyLinkedin}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded text-sm text-white transition-colors"
                >
                  <Linkedin className="w-4 h-4 text-blue-500" />
                  Company LinkedIn
                </a>
              )}
              {!deal.companyWebsite && !deal.companyLinkedin && (
                <span className="text-slate-500 italic text-sm">No company links available</span>
              )}
            </div>
          </div>

          {/* Founders */}
          {deal.founders && deal.founders.length > 0 && (
            <div>
              <label className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-2">
                Founders
              </label>
              <div className="space-y-2">
                {deal.founders.map((founder, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between p-3 bg-slate-900/50 border border-slate-800 rounded"
                  >
                    <div className="flex items-center gap-2">
                      <User className="w-4 h-4 text-slate-400" />
                      <span className="text-white">{founder.name}</span>
                      {founder.title && (
                        <span className="text-slate-500 text-sm">• {founder.title}</span>
                      )}
                    </div>
                    {founder.linkedinUrl && (
                      <a
                        href={founder.linkedinUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 text-blue-400 hover:text-blue-300 text-sm transition-colors"
                      >
                        <Linkedin className="w-3 h-3" />
                        LinkedIn
                      </a>
                    )}
                  </div>
                ))}
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
        <div className="px-6 py-4 border-t border-slate-800 flex justify-end">
          <button onClick={onClose} className="btn-secondary">
            Close
          </button>
        </div>
      </div>
    </>
  );
}

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

export default DealModal;
