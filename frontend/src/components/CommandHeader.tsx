/**
 * CommandHeader - Minimal header with just logo
 */

import { Menu } from 'lucide-react';

interface CommandHeaderProps {
  systemStatus: unknown;
  onSettingsClick: () => void;
  onPreFundingClick?: () => void;
  onMenuClick?: () => void;
}

export function CommandHeader({
  onMenuClick,
}: CommandHeaderProps) {
  return (
    <header className="h-14 border-b border-slate-800/50 bg-black flex items-center px-4 shrink-0 z-20">
      {/* Menu button (mobile) + Logo */}
      <div className="flex items-center gap-3">
        {onMenuClick && (
          <button
            onClick={onMenuClick}
            className="md:hidden flex items-center justify-center w-10 h-10 -ml-2 text-slate-400 hover:text-slate-200 transition-colors"
            aria-label="Toggle menu"
          >
            <Menu className="w-5 h-5" />
          </button>
        )}
        <div className="w-2 h-2 bg-[#0071e3] rounded-full" />
        <h1 className="text-[15px] font-medium text-white">
          Eagle Eye
        </h1>
      </div>
    </header>
  );
}

export default CommandHeader;
