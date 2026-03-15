/**
 * TrackerCard - A draggable card in the Tracker Kanban
 *
 * Uses @dnd-kit/sortable for drag-and-drop
 * Entire card is draggable, click only fires if not dragging
 */

import { useRef } from 'react';
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

  // Track drag state to prevent click after drag
  const dragStartedRef = useRef(false);
  const mouseDownPosRef = useRef<{ x: number; y: number } | null>(null);

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const isCurrentlyDragging = isDragging || isSortableDragging;

  // Track when drag starts
  if (isCurrentlyDragging && !dragStartedRef.current) {
    dragStartedRef.current = true;
  }

  // Reset drag state when not dragging
  if (!isCurrentlyDragging && dragStartedRef.current) {
    // Delay reset to prevent click from firing
    setTimeout(() => {
      dragStartedRef.current = false;
    }, 50);
  }

  const handleMouseDown = (e: React.MouseEvent) => {
    mouseDownPosRef.current = { x: e.clientX, y: e.clientY };
  };

  const handleClick = (e: React.MouseEvent) => {
    // Don't open modal if we were dragging
    if (dragStartedRef.current) {
      e.preventDefault();
      e.stopPropagation();
      return;
    }

    // Check if mouse moved significantly (indicates drag attempt)
    if (mouseDownPosRef.current) {
      const dx = Math.abs(e.clientX - mouseDownPosRef.current.x);
      const dy = Math.abs(e.clientY - mouseDownPosRef.current.y);
      if (dx > 5 || dy > 5) {
        return; // Mouse moved, was probably dragging
      }
    }

    onClick?.();
  };

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
      onMouseDown={handleMouseDown}
      onClick={handleClick}
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
        {item.lastContactDate ? (
          <div className="flex items-center gap-1">
            <Calendar className="w-3 h-3" />
            <span>{formatDate(item.lastContactDate)}</span>
          </div>
        ) : (
          <span className="text-slate-600">No contact yet</span>
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
            <a
              href={item.website}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="text-slate-400 hover:text-emerald-400 transition-colors"
              title={item.website}
              aria-label={`Visit ${item.companyName} website`}
            >
              <ExternalLink className="w-3 h-3" aria-hidden="true" />
            </a>
          )}
        </div>
      </div>

      {/* Next Step indicator */}
      {item.nextStep && (
        <div className="mt-2 pt-2 border-t border-slate-700 text-xs text-amber-400 truncate">
          Next: {item.nextStep}
        </div>
      )}
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

export default TrackerCard;
