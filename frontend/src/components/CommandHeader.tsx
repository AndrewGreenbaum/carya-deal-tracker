/**
 * CommandHeader - Top status bar with system indicators
 *
 * Shows:
 * - Logo with pulsing indicator
 * - SEC EDGAR status
 * - Scrapers status (18/18 ACTIVE)
 * - Claude 3.5 status
 * - Next scrape countdown (live updating every second)
 *
 * Command Center dark theme
 */

import { useState, useEffect } from 'react';
import { Database, Server, Cpu, Settings, RefreshCw, Clock } from 'lucide-react';
import type { SystemStatus } from '../types';

interface CommandHeaderProps {
  systemStatus: SystemStatus;
  onRefresh: () => void;
  onSettingsClick: () => void;
  isRefreshing?: boolean;
}

export function CommandHeader({
  systemStatus,
  onRefresh,
  onSettingsClick,
  isRefreshing = false,
}: CommandHeaderProps) {
  // Live countdown state - updates every second
  const [countdown, setCountdown] = useState<string | null>(null);

  // Calculate countdown to next scrape
  const calculateCountdown = (): string | null => {
    if (!systemStatus.nextScrape) return null;

    const nextRun = new Date(systemStatus.nextScrape);
    const now = new Date();
    const diffMs = nextRun.getTime() - now.getTime();

    if (diffMs <= 0) return 'Running...';

    const hours = Math.floor(diffMs / 3600000);
    const minutes = Math.floor((diffMs % 3600000) / 60000);
    const seconds = Math.floor((diffMs % 60000) / 1000);

    if (hours > 0) {
      return `${hours}h ${minutes.toString().padStart(2, '0')}m ${seconds.toString().padStart(2, '0')}s`;
    }
    return `${minutes}m ${seconds.toString().padStart(2, '0')}s`;
  };

  // Update countdown every second
  useEffect(() => {
    // Initial calculation
    setCountdown(calculateCountdown());

    // Update every second
    const interval = setInterval(() => {
      setCountdown(calculateCountdown());
    }, 1000);

    return () => clearInterval(interval);
  }, [systemStatus.nextScrape]);

  const nextScrape = countdown;

  return (
    <header className="h-16 border-b border-slate-800 bg-[#0a0a0c] flex items-center justify-between px-6 shrink-0 z-20">
      {/* Logo */}
      <div className="flex items-center gap-4">
        <div className="w-3 h-3 bg-emerald-500 rounded-full pulse-glow" />
        <h1 className="text-xl font-bold tracking-tighter text-white">
          bud tracker _ DIRTY FUCKING THEO
        </h1>
      </div>

      {/* Status Indicators */}
      <div className="flex gap-4 text-xs font-medium">
        {/* SEC EDGAR */}
        <StatusBadge
          icon={<Database className="w-3 h-3 text-blue-400" />}
          label="SEC EDGAR"
          value={systemStatus.secEdgar.status === 'live' ? 'LIVE STREAM' : 'IDLE'}
          valueColor={systemStatus.secEdgar.status === 'live' ? 'text-emerald-400' : 'text-slate-400'}
        />

        {/* Scrapers */}
        <StatusBadge
          icon={<Server className="w-3 h-3 text-purple-400" />}
          label="SCRAPERS"
          value={systemStatus.scrapers.status === 'live' ? '18/18 ACTIVE' : 'IDLE'}
          valueColor={systemStatus.scrapers.status === 'live' ? 'text-white' : 'text-slate-400'}
        />

        {/* Claude Status */}
        <StatusBadge
          icon={<Cpu className="w-3 h-3 text-orange-400" />}
          label="CLAUDE 3.5"
          value={systemStatus.claude.status.toUpperCase()}
          valueColor={
            systemStatus.claude.status === 'processing'
              ? 'text-orange-400'
              : systemStatus.claude.status === 'error'
              ? 'text-red-400'
              : 'text-white'
          }
        />

        {/* Next Scrape Countdown */}
        {nextScrape && (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 rounded border border-slate-800">
            <Clock className="w-3 h-3 text-slate-400" />
            <span className="text-slate-400">NEXT:</span>
            <span className={`font-mono ${nextScrape === 'Running...' ? 'text-orange-400 animate-pulse' : 'text-emerald-400'}`}>
              {nextScrape}
            </span>
          </div>
        )}

        {/* Action Buttons */}
        <button
          onClick={onRefresh}
          disabled={isRefreshing}
          className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 rounded border border-slate-800 hover:border-emerald-500/50 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3 h-3 ${isRefreshing ? 'animate-spin' : ''}`} />
        </button>

        <button
          onClick={onSettingsClick}
          className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 rounded border border-slate-800 hover:border-emerald-500/50 transition-colors"
        >
          <Settings className="w-3 h-3" />
        </button>
      </div>
    </header>
  );
}

interface StatusBadgeProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  valueColor: string;
}

function StatusBadge({ icon, label, value, valueColor }: StatusBadgeProps) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900 rounded border border-slate-800">
      {icon}
      <span className="text-slate-400">{label}:</span>
      <span className={valueColor}>{value}</span>
    </div>
  );
}

export default CommandHeader;
