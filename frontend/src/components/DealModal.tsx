import { X, Building2, Shield, Layers, Bot, Database, Copy, Check } from 'lucide-react';
import { useState } from 'react';
import type { Deal, EnterpriseCategory } from '../types';

interface DealModalProps {
  deal: Deal;
  onClose: () => void;
  darkMode?: boolean;
}

function CategoryBadge({ category, darkMode }: { category?: EnterpriseCategory; darkMode?: boolean }) {
  const categoryConfig: Record<string, { label: string; icon: typeof Building2; color: string; bg: string }> = {
    infrastructure: { label: 'Infrastructure', icon: Layers, color: 'text-blue-500', bg: 'bg-blue-100' },
    security: { label: 'Security', icon: Shield, color: 'text-red-500', bg: 'bg-red-100' },
    vertical_saas: { label: 'Vertical SaaS', icon: Building2, color: 'text-green-500', bg: 'bg-green-100' },
    agentic: { label: 'Agentic', icon: Bot, color: 'text-purple-500', bg: 'bg-purple-100' },
    data_intelligence: { label: 'Data Intelligence', icon: Database, color: 'text-amber-500', bg: 'bg-amber-100' },
  };

  if (!category || category === 'not_enterprise') {
    return <span className={darkMode ? 'text-gray-500' : 'text-gray-400'}>Not Enterprise AI</span>;
  }

  const config = categoryConfig[category];
  if (!config) return null;

  const Icon = config.icon;
  return (
    <div className={`inline-flex items-center gap-2 px-3 py-1 rounded-full ${darkMode ? 'bg-gray-800' : config.bg}`}>
      <Icon size={16} className={config.color} />
      <span className={`text-sm font-medium ${config.color}`}>{config.label}</span>
    </div>
  );
}

function formatStage(stage: string): string {
  const stageMap: Record<string, string> = {
    pre_seed: 'Pre-Seed',
    seed: 'Seed',
    series_a: 'Series A',
    series_b: 'Series B',
    series_c: 'Series C',
    series_d: 'Series D',
    growth: 'Growth',
    exit: 'Exit',
    unknown: 'Unknown',
  };
  return stageMap[stage] || stage;
}

function formatDate(dateStr: string): string {
  if (!dateStr || dateStr === 'DD/MM/YYYY') return '-';
  try {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
      year: 'numeric'
    });
  } catch {
    return dateStr;
  }
}

export function DealModal({ deal, onClose, darkMode = false }: DealModalProps) {
  const [copied, setCopied] = useState(false);

  const handleCopySnippet = async () => {
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={handleBackdropClick}
    >
      <div className={`w-full max-w-2xl mx-4 rounded-xl shadow-2xl ${
        darkMode ? 'bg-gray-900 border border-gray-700' : 'bg-white'
      }`}>
        {/* Header */}
        <div className={`flex items-center justify-between px-6 py-4 border-b ${
          darkMode ? 'border-gray-700' : 'border-gray-200'
        }`}>
          <h2 className={`text-xl font-semibold ${darkMode ? 'text-white' : 'text-gray-900'}`}>
            {deal.startupName}
          </h2>
          <button
            onClick={onClose}
            className={`p-2 rounded-lg transition-colors ${
              darkMode
                ? 'hover:bg-gray-800 text-gray-400 hover:text-gray-200'
                : 'hover:bg-gray-100 text-gray-500 hover:text-gray-700'
            }`}
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="px-6 py-6 space-y-6">
          {/* Key Details Grid */}
          <div className="grid grid-cols-2 gap-6">
            <div>
              <div className={`text-sm font-medium mb-1 ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
                Round
              </div>
              <div className={`text-lg font-semibold ${darkMode ? 'text-white' : 'text-gray-900'}`}>
                {formatStage(deal.investmentStage)}
              </div>
            </div>
            <div>
              <div className={`text-sm font-medium mb-1 ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
                Amount
              </div>
              <div className={`text-lg font-semibold ${darkMode ? 'text-white' : 'text-gray-900'}`}>
                {deal.amountInvested}
              </div>
            </div>
            <div>
              <div className={`text-sm font-medium mb-1 ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
                Date
              </div>
              <div className={darkMode ? 'text-gray-300' : 'text-gray-700'}>
                {formatDate(deal.date)}
              </div>
            </div>
            <div>
              <div className={`text-sm font-medium mb-1 ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
                Category
              </div>
              <CategoryBadge category={deal.enterpriseCategory} darkMode={darkMode} />
            </div>
          </div>

          {/* Lead Investor */}
          {deal.leadInvestor && (
            <div>
              <div className={`text-sm font-medium mb-2 ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
                Lead Investor
              </div>
              <div className={`flex items-center gap-2 ${darkMode ? 'text-white' : 'text-gray-900'}`}>
                <div className={`w-3 h-3 rounded-full ${darkMode ? 'bg-white' : 'bg-gray-900'}`} />
                <span className="font-medium">{deal.leadInvestor}</span>
                {deal.leadPartner && (
                  <span className={darkMode ? 'text-gray-400' : 'text-gray-500'}>
                    ({deal.leadPartner})
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Verification Snippet */}
          {deal.verificationSnippet && (
            <div>
              <div className={`text-sm font-medium mb-2 ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
                Verification Snippet
              </div>
              <div className={`relative p-4 rounded-lg ${
                darkMode ? 'bg-gray-800' : 'bg-gray-50'
              }`}>
                <p className={`italic pr-10 ${darkMode ? 'text-gray-300' : 'text-gray-700'}`}>
                  "{deal.verificationSnippet}"
                </p>
                <button
                  onClick={handleCopySnippet}
                  className={`absolute top-3 right-3 p-2 rounded transition-colors ${
                    darkMode
                      ? 'hover:bg-gray-700 text-gray-400'
                      : 'hover:bg-gray-200 text-gray-500'
                  }`}
                  title="Copy to clipboard"
                >
                  {copied ? <Check size={16} className="text-green-500" /> : <Copy size={16} />}
                </button>
              </div>
            </div>
          )}

          {/* Enterprise AI Badge */}
          <div className="flex items-center gap-2">
            <div className={`px-3 py-1 rounded-full text-sm font-medium ${
              deal.isEnterpriseAi
                ? darkMode
                  ? 'bg-green-900 text-green-300'
                  : 'bg-green-100 text-green-700'
                : darkMode
                  ? 'bg-gray-800 text-gray-400'
                  : 'bg-gray-100 text-gray-500'
            }`}>
              {deal.isEnterpriseAi ? 'Enterprise AI' : 'Consumer/Other'}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className={`px-6 py-4 border-t ${
          darkMode ? 'border-gray-700' : 'border-gray-200'
        }`}>
          <button
            onClick={onClose}
            className={`w-full py-2 px-4 rounded-lg font-medium transition-colors ${
              darkMode
                ? 'bg-gray-800 text-gray-300 hover:bg-gray-700'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
