/**
 * TrackerColumn - A Kanban column for the Tracker
 *
 * Uses @dnd-kit for drag-and-drop functionality
 */

import { useDroppable } from '@dnd-kit/core';
import {
  SortableContext,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';

import { TrackerCard } from './TrackerCard';
import type { TrackerItem, TrackerStatus } from '../types';
import { TRACKER_STATUS_COLORS } from '../types';

interface TrackerColumnProps {
  id: TrackerStatus;
  title: string;
  items: TrackerItem[];
  count: number;
  onEditItem: (item: TrackerItem) => void;
}

export function TrackerColumn({
  id,
  title,
  items,
  count,
  onEditItem,
}: TrackerColumnProps) {
  const { setNodeRef, isOver } = useDroppable({
    id,
  });

  const color = TRACKER_STATUS_COLORS[id];
  const colorClasses: Record<string, string> = {
    slate: 'bg-slate-500',
    blue: 'bg-blue-500',
    amber: 'bg-amber-500',
    emerald: 'bg-emerald-500',
    green: 'bg-green-500',
    red: 'bg-red-500',
  };

  return (
    <div className="flex flex-col w-72 shrink-0">
      {/* Column Header */}
      <div className="flex items-center gap-2 mb-3 px-1">
        <div className={`w-2 h-2 rounded-full ${colorClasses[color]}`} />
        <h3 className="text-sm font-medium text-white">{title}</h3>
        <span className="ml-auto px-2 py-0.5 text-xs bg-slate-800 text-slate-400 rounded-full">
          {count}
        </span>
      </div>

      {/* Column Body */}
      <div
        ref={setNodeRef}
        role="region"
        aria-label={`${title} column, ${count} items`}
        className={`flex-1 rounded-lg p-2 transition-colors min-h-[200px] ${
          isOver ? 'bg-slate-800/50 ring-2 ring-emerald-500/30' : 'bg-slate-900/30'
        }`}
      >
        <SortableContext
          items={items.map((i) => i.id)}
          strategy={verticalListSortingStrategy}
        >
          <div className="space-y-2">
            {items.map((item) => (
              <TrackerCard
                key={item.id}
                item={item}
                onClick={() => onEditItem(item)}
              />
            ))}
          </div>
        </SortableContext>

        {/* Empty State */}
        {items.length === 0 && (
          <div className="flex flex-col items-center justify-center h-24 text-center px-4">
            <span className="text-slate-500 text-sm">
              {id === 'watching' && 'No companies watching'}
              {id === 'reached_out' && 'No outreach yet'}
              {id === 'in_conversation' && 'No active conversations'}
              {id === 'closing_spv' && 'No deals closing'}
              {id === 'spv_complete' && 'No completed SPVs'}
              {id === 'spv_rejected' && 'No rejected deals'}
            </span>
            <span className="text-slate-600 text-xs mt-1">
              {id === 'watching' ? 'Add a company or drag deals here' : 'Drag items here'}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

export default TrackerColumn;
