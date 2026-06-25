/**
 * StatsCards - Simple page header
 */

import { memo } from 'react';
import type { DashboardStats } from '../types';

interface StatsCardsProps {
  stats: DashboardStats;
}

export const StatsCards = memo(function StatsCards({ stats }: StatsCardsProps) {
  return (
    <div className="px-6 py-4 bg-[#0a0a0c]">
      <h2 className="text-lg font-medium text-slate-200 tracking-tight">
        Deals
      </h2>
      <p className="text-xs text-slate-600 mt-0.5">
        {stats.newDeals24h.toLocaleString()} companies tracked
      </p>
    </div>
  );
});

export default StatsCards;
