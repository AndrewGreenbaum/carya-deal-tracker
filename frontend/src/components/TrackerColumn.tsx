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
          <div className="flex items-center justify-center h-24 text-slate-600 text-sm">
            Drop items here
          </div>
        )}
      </div>
    </div>
  );
}

export default TrackerColumn;
