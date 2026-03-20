/**
 * TrackerCard - A draggable card in the Tracker Kanban
 * Uses @dnd-kit/sortable with distance activation (8px) to prevent click conflicts
 */

import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, ExternalLink, Calendar, Link } from 'lucide-react';
import type { TrackerItem } from '../types';

interface TrackerCardProps {
  item: TrackerItem;
  isDragging?: boolean;
  onClick?: () => void;
}

export function TrackerCard({ item, isDragging, onClick }: TrackerCardProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging: isSortableDragging,
  } = useSortable({ id: item.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const isCurrentlyDragging = isDragging || isSortableDragging;

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      role="article"
      aria-label={`${item.companyName}${item.leadInvestor ? `, led by ${item.leadInvestor}` : ''}`}
      className={`
        bg-slate-800 border border-slate-700 rounded-lg p-3 cursor-grab
        hover:border-slate-600 hover:bg-slate-750 transition-all select-none
        ${isCurrentlyDragging ? 'opacity-50 shadow-lg ring-2 ring-emerald-500/30 cursor-grabbing' : ''}
      `}
      onClick={() => !isCurrentlyDragging && onClick?.()}
    >
      {/* Header with drag handle indicator */}
      <div className="flex items-start gap-2">
        <div className="p-0.5 text-slate-500 shrink-0" aria-hidden="true">
          <GripVertical className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-medium text-white truncate">
            {item.companyName}
          </h4>
          {item.roundType && (
            <span className="text-xs text-slate-400">
              {item.roundType}
              {item.amount && ` - ${item.amount}`}
            </span>
          )}
        </div>
      </div>

      {/* Lead Investor */}
      {item.leadInvestor && (
        <div className="mt-2 text-xs text-slate-500">
          Lead: <span className="text-slate-400">{item.leadInvestor}</span>
        </div>
      )}

      {/* Footer with metadata */}
      <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
        {/* Last Contact */}
        {item.lastContactDate && (
          <div className="flex items-center gap-1">
            <Calendar className="w-3 h-3" />
            <span>{formatDate(item.lastContactDate)}</span>
          </div>
        )}

        {/* Icons */}
        <div className="flex items-center gap-2">
          {/* Deal Link Indicator */}
          {item.dealId && (
            <span
              className="text-blue-400"
              title="Linked to Dashboard deal"
              aria-label="Linked to Dashboard deal"
            >
              <Link className="w-3 h-3" aria-hidden="true" />
            </span>
          )}

          {/* Website Link */}
          {item.website && (
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                e.preventDefault();
                window.open(item.website, '_blank', 'noopener,noreferrer');
              }}
              className="text-slate-400 hover:text-emerald-400 transition-colors cursor-pointer"
              title={`Open ${item.website}`}
              aria-label={`Visit ${item.companyName} website`}
            >
              <ExternalLink className="w-3 h-3" aria-hidden="true" />
            </button>
          )}
        </div>
      </div>

      {/* Next Step indicator */}
      {item.nextStep && (
        <div className="mt-2 pt-2 border-t border-slate-700 text-xs text-amber-400 truncate">
          Next: {item.nextStep}
        </div>
      )}

      {/* Added date - unobtrusive */}
      <div className="mt-2 text-[10px] text-slate-600">
        Added {formatAddedDate(item.createdAt)}
      </div>
    </div>
  );
}

function formatDate(dateString: string): string {
  try {
    const date = new Date(dateString);
    const now = new Date();
    const diffDays = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays}d ago`;
    if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return dateString;
  }
}

function formatAddedDate(dateString: string): string {
  try {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}

export default TrackerCard;
