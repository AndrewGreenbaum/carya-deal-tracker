/**
 * AdminFeedback.tsx - Simple admin page to view and manage feedback
 * Access via Ctrl+9 secret shortcut
 */

import { useState, useEffect, useCallback } from 'react';
import { Check, RefreshCw, Flag, MessageSquare } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE || 'https://bud-tracker-backend-production.up.railway.app';
const API_KEY = 'dev-key';

interface FeedbackItem {
  id: number;
  feedback_type: 'flag' | 'suggestion';
  company_name: string;
  deal_id: number | null;
  reason: string | null;
  suggestion_type: string | null;
  source_url: string | null;
  reporter_email: string | null;
  reviewed: boolean;
  created_at: string;
}

export function AdminFeedback() {
  const [feedback, setFeedback] = useState<FeedbackItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'unreviewed'>('unreviewed');
  const [authenticated, setAuthenticated] = useState(false);

  // Password check on mount
  useEffect(() => {
    const pwd = prompt('Enter admin password:');
    if (pwd === 'theo2025') {
      setAuthenticated(true);
    } else {
      alert('Access denied');
      window.location.href = '/';
    }
  }, []);

  const fetchFeedback = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = filter === 'unreviewed' ? '?reviewed=false' : '';
      const url = `${API_BASE}/feedback${params}`;
      const res = await fetch(url, {
        headers: { 'X-API-Key': API_KEY },
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }
      const data = await res.json();
      setFeedback(data.items || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    }
    setLoading(false);
  }, [filter]);

  useEffect(() => {
    if (authenticated) {
      fetchFeedback();
    }
  }, [authenticated, fetchFeedback]);

  const markReviewed = async (id: number) => {
    try {
      await fetch(`${API_BASE}/feedback/${id}/reviewed`, {
        method: 'PATCH',
        headers: { 'X-API-Key': API_KEY },
      });
      setFeedback(prev => prev.map(f =>
        f.id === id ? { ...f, reviewed: true } : f
      ));
    } catch (err) {
      console.error('Failed to mark reviewed:', err);
    }
  };

  if (!authenticated) {
    return <div className="min-h-screen bg-black" />;
  }

  return (
    <div className="min-h-screen bg-black text-green-400 p-8 font-mono">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <h1 className="text-2xl font-bold text-green-500">
            FEEDBACK ADMIN
          </h1>
          <div className="flex gap-4">
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value as 'all' | 'unreviewed')}
              className="bg-gray-900 border border-green-900 rounded px-3 py-2 text-green-400"
            >
              <option value="unreviewed">Unreviewed Only</option>
              <option value="all">All Feedback</option>
            </select>
            <button
              onClick={fetchFeedback}
              className="flex items-center gap-2 px-4 py-2 bg-green-900/30 border border-green-700 rounded hover:bg-green-900/50"
            >
              <RefreshCw className="w-4 h-4" />
              Refresh
            </button>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-4 mb-8">
          <div className="bg-gray-900 border border-green-900 rounded-lg p-4">
            <div className="text-3xl font-bold">{feedback.length}</div>
            <div className="text-green-600 text-sm">Total Items</div>
          </div>
          <div className="bg-gray-900 border border-green-900 rounded-lg p-4">
            <div className="text-3xl font-bold">
              {feedback.filter(f => f.feedback_type === 'flag').length}
            </div>
            <div className="text-red-500 text-sm">Flags</div>
          </div>
          <div className="bg-gray-900 border border-green-900 rounded-lg p-4">
            <div className="text-3xl font-bold">
              {feedback.filter(f => f.feedback_type === 'suggestion').length}
            </div>
            <div className="text-blue-400 text-sm">Suggestions</div>
          </div>
        </div>

        {/* Feedback List */}
        {loading ? (
          <div className="text-center py-12 text-green-600">Loading feedback...</div>
        ) : error ? (
          <div className="text-center py-12">
            <div className="text-red-400 mb-4">Error: {error}</div>
            <button
              onClick={fetchFeedback}
              className="px-4 py-2 bg-green-900/30 border border-green-700 rounded hover:bg-green-900/50"
            >
              Retry
            </button>
          </div>
        ) : feedback.length === 0 ? (
          <div className="text-center py-12 text-green-600">
            No feedback to review
          </div>
        ) : (
          <div className="space-y-4">
            {feedback.map((item) => (
              <div
                key={item.id}
                className={`bg-gray-900 border rounded-lg p-4 ${
                  item.reviewed ? 'border-gray-800 opacity-60' : 'border-green-900'
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    {/* Type badge */}
                    <div className="flex items-center gap-2 mb-2">
                      {item.feedback_type === 'flag' ? (
                        <span className="flex items-center gap-1 px-2 py-1 bg-red-900/30 text-red-400 text-xs rounded">
                          <Flag className="w-3 h-3" />
                          FLAG
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 px-2 py-1 bg-blue-900/30 text-blue-400 text-xs rounded">
                          <MessageSquare className="w-3 h-3" />
                          {item.suggestion_type?.toUpperCase() || 'SUGGESTION'}
                        </span>
                      )}
                      {item.reviewed && (
                        <span className="px-2 py-1 bg-green-900/30 text-green-400 text-xs rounded">
                          REVIEWED
                        </span>
                      )}
                    </div>

                    {/* Company name */}
                    <h3 className="text-lg font-semibold text-white mb-1">
                      {item.company_name}
                    </h3>

                    {/* Reason/details */}
                    {item.reason && (
                      <p className="text-green-300 mb-2">{item.reason}</p>
                    )}

                    {/* Metadata */}
                    <div className="flex gap-4 text-xs text-green-600">
                      <span>
                        {new Date(item.created_at).toLocaleDateString()} {new Date(item.created_at).toLocaleTimeString()}
                      </span>
                      {item.deal_id && <span>Deal #{item.deal_id}</span>}
                      {item.source_url && (
                        <a
                          href={item.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-400 hover:underline"
                        >
                          Source
                        </a>
                      )}
                      {item.reporter_email && (
                        <span>From: {item.reporter_email}</span>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  {!item.reviewed && (
                    <button
                      onClick={() => markReviewed(item.id)}
                      className="flex items-center gap-1 px-3 py-2 bg-green-900/30 border border-green-700 rounded hover:bg-green-900/50 text-sm"
                    >
                      <Check className="w-4 h-4" />
                      Mark Reviewed
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Back link */}
        <div className="mt-8 text-center">
          <a href="/" className="text-green-600 hover:text-green-400">
            ← Back to Dashboard
          </a>
        </div>
      </div>
    </div>
  );
}
