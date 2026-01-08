/**
 * Tracker - Kanban-style CRM for managing deal pipeline
 *
 * Features:
 * - 4 columns: Watching, Reached Out, In Conversation, Closing SPV
 * - Drag-and-drop between columns
 * - Manual company entry
 * - Links to deals from Dashboard
 */

import { useState, useEffect, useCallback } from 'react';
import {
  DndContext,
  DragOverlay,
  closestCorners,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
  type DragOverEvent,
} from '@dnd-kit/core';
import { sortableKeyboardCoordinates } from '@dnd-kit/sortable';
import { Plus, RefreshCw } from 'lucide-react';

import { TrackerColumn } from './TrackerColumn';
import { TrackerCard } from './TrackerCard';
import { TrackerModal } from './TrackerModal';
import type { TrackerItem, TrackerStatus, TrackerStats } from '../types';
import { TRACKER_STATUS_LABELS } from '../types';
import {
  fetchTrackerItems,
  createTrackerItem,
  updateTrackerItem,
  moveTrackerItem,
  deleteTrackerItem,
} from '../api/deals';

const COLUMNS: TrackerStatus[] = ['watching', 'reached_out', 'in_conversation', 'closing_spv'];

export function Tracker() {
  const [items, setItems] = useState<TrackerItem[]>([]);
  const [stats, setStats] = useState<TrackerStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeItem, setActiveItem] = useState<TrackerItem | null>(null);
  const [modalItem, setModalItem] = useState<TrackerItem | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const loadItems = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await fetchTrackerItems();
      setItems(response.items);
      setStats(response.stats);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tracker items');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadItems();
  }, [loadItems]);

  const getItemsByStatus = (status: TrackerStatus): TrackerItem[] => {
    return items
      .filter((item) => item.status === status)
      .sort((a, b) => a.position - b.position);
  };

  const handleDragStart = (event: DragStartEvent) => {
    const { active } = event;
    const item = items.find((i) => i.id === active.id);
    if (item) {
      setActiveItem(item);
    }
  };

  const handleDragOver = (event: DragOverEvent) => {
    const { active, over } = event;
    if (!over) return;

    const activeItem = items.find((i) => i.id === active.id);
    if (!activeItem) return;

    // Check if we're over a column or another card
    const overId = over.id;
    let overStatus: TrackerStatus;

    if (COLUMNS.includes(overId as TrackerStatus)) {
      overStatus = overId as TrackerStatus;
    } else {
      const overItem = items.find((i) => i.id === overId);
      if (!overItem) return;
      overStatus = overItem.status;
    }

    if (activeItem.status !== overStatus) {
      // Move to new column - update local state optimistically
      setItems((prev) => {
        const updatedItems = prev.map((item) =>
          item.id === activeItem.id ? { ...item, status: overStatus } : item
        );
        return updatedItems;
      });
    }
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveItem(null);

    if (!over) return;

    const activeItem = items.find((i) => i.id === active.id);
    if (!activeItem) return;

    let targetStatus: TrackerStatus;
    let targetPosition: number;

    if (COLUMNS.includes(over.id as TrackerStatus)) {
      // Dropped on a column
      targetStatus = over.id as TrackerStatus;
      const columnItems = getItemsByStatus(targetStatus);
      targetPosition = columnItems.length;
    } else {
      // Dropped on another card
      const overItem = items.find((i) => i.id === over.id);
      if (!overItem) return;

      targetStatus = overItem.status;
      const columnItems = getItemsByStatus(targetStatus);
      const overIndex = columnItems.findIndex((i) => i.id === over.id);
      targetPosition = overIndex >= 0 ? overIndex : columnItems.length;
    }

    // Update local state optimistically
    setItems((prev) => {
      const updated = prev.map((item) =>
        item.id === activeItem.id
          ? { ...item, status: targetStatus, position: targetPosition }
          : item
      );
      // Re-sort positions within the column
      const columnItems = updated
        .filter((i) => i.status === targetStatus)
        .sort((a, b) => a.position - b.position);

      return updated.map((item) => {
        if (item.status === targetStatus) {
          const idx = columnItems.findIndex((c) => c.id === item.id);
          return { ...item, position: idx };
        }
        return item;
      });
    });

    // Sync with backend
    try {
      await moveTrackerItem(activeItem.id, targetStatus, targetPosition);
    } catch (err) {
      console.error('Failed to move item:', err);
      // Reload to get correct state
      loadItems();
    }
  };

  const handleCreateNew = () => {
    setModalItem(null);
    setIsCreating(true);
    setIsModalOpen(true);
  };

  const handleEditItem = (item: TrackerItem) => {
    setModalItem(item);
    setIsCreating(false);
    setIsModalOpen(true);
  };

  const handleSaveItem = async (data: Partial<TrackerItem>) => {
    try {
      if (isCreating) {
        await createTrackerItem({
          companyName: data.companyName!,
          roundType: data.roundType,
          amount: data.amount,
          leadInvestor: data.leadInvestor,
          website: data.website,
          notes: data.notes,
          status: 'watching',
        });
      } else if (modalItem) {
        await updateTrackerItem(modalItem.id, {
          companyName: data.companyName,
          roundType: data.roundType,
          amount: data.amount,
          leadInvestor: data.leadInvestor,
          website: data.website,
          notes: data.notes,
          lastContactDate: data.lastContactDate,
          nextStep: data.nextStep,
        });
      }
      setIsModalOpen(false);
      loadItems();
    } catch (err) {
      console.error('Failed to save item:', err);
      setError(err instanceof Error ? err.message : 'Failed to save item');
    }
  };

  const handleDeleteItem = async (itemId: number) => {
    try {
      await deleteTrackerItem(itemId);
      setIsModalOpen(false);
      loadItems();
    } catch (err) {
      console.error('Failed to delete item:', err);
      setError(err instanceof Error ? err.message : 'Failed to delete item');
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <RefreshCw className="w-6 h-6 text-slate-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
        <div>
          <h1 className="text-xl font-bold text-white">Deal Tracker</h1>
          <p className="text-sm text-slate-400">
            {stats?.total || 0} companies in pipeline
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={loadItems}
            className="p-2 text-slate-400 hover:text-white hover:bg-slate-800 rounded transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={handleCreateNew}
            className="flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded font-medium transition-colors"
          >
            <Plus className="w-4 h-4" />
            Add Company
          </button>
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="mx-6 mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded text-red-400 text-sm">
          {error}
          <button
            onClick={() => setError(null)}
            className="ml-2 underline hover:no-underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Kanban Board */}
      <div className="flex-1 overflow-x-auto p-6">
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
          onDragEnd={handleDragEnd}
        >
          <div className="flex gap-4 min-h-full">
            {COLUMNS.map((status) => (
              <TrackerColumn
                key={status}
                id={status}
                title={TRACKER_STATUS_LABELS[status]}
                items={getItemsByStatus(status)}
                count={stats?.[status] || 0}
                onEditItem={handleEditItem}
              />
            ))}
          </div>

          <DragOverlay>
            {activeItem ? (
              <TrackerCard item={activeItem} isDragging />
            ) : null}
          </DragOverlay>
        </DndContext>
      </div>

      {/* Modal */}
      {isModalOpen && (
        <TrackerModal
          item={modalItem}
          isCreating={isCreating}
          onClose={() => setIsModalOpen(false)}
          onSave={handleSaveItem}
          onDelete={modalItem ? () => handleDeleteItem(modalItem.id) : undefined}
        />
      )}
    </div>
  );
}

export default Tracker;
