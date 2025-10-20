import { useState, useEffect } from 'react';
import { Ghost, ExternalLink, RefreshCw, CheckCircle, AlertCircle } from 'lucide-react';

interface StealthDetection {
  id: number;
  fund_slug: string;
  detected_url: string;
  detected_at: string;
  company_name: string | null;
  is_confirmed: boolean;
  notes: string | null;
}

interface StealthTrackerProps {
  darkMode?: boolean;
}

// Use environment variable for API base URL
const API_BASE = import.meta.env.VITE_API_BASE || '/api';

async function fetchStealthDetections(): Promise<{ detections: StealthDetection[]; total: number }> {
  const response = await fetch(`${API_BASE}/stealth-detections?limit=100`);
  if (!response.ok) {
    throw new Error(`Failed to fetch: ${response.statusText}`);
  }
  return response.json();
}

function formatDate(dateStr: string): string {
  try {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return dateStr;
  }
}

function formatFundName(slug: string): string {
  const fundNames: Record<string, string> = {
    a16z: 'a16z',
    sequoia: 'Sequoia',
    benchmark: 'Benchmark',
    founders_fund: 'Founders Fund',
    thrive: 'Thrive Capital',
    khosla: 'Khosla Ventures',
    index: 'Index Ventures',
    insight: 'Insight Partners',
    bessemer: 'Bessemer VP',
    redpoint: 'Redpoint',
    greylock: 'Greylock',
    gv: 'GV',
    menlo: 'Menlo Ventures',
    usv: 'USV',
    accel: 'Accel',
    felicis: 'Felicis',
    general_catalyst: 'General Catalyst',
    first_round: 'First Round',
  };
  return fundNames[slug] || slug;
}

export function StealthTracker({ darkMode = false }: StealthTrackerProps) {
  const [detections, setDetections] = useState<StealthDetection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadDetections = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchStealthDetections();
      setDetections(data.detections);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDetections();
  }, []);

  return (
    <div className="flex-1 overflow-auto">
      {/* Header */}
      <div className={`px-6 py-4 border-b sticky top-0 z-10 ${
        darkMode
          ? 'bg-gray-900 border-gray-700'
          : 'bg-white border-border-light'
      }`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Ghost size={24} className={darkMode ? 'text-gray-400' : 'text-gray-600'} />
            <h2 className={`text-lg font-semibold ${darkMode ? 'text-white' : 'text-gray-900'}`}>
              Stealth Tracker
            </h2>
          </div>
          <button
            onClick={loadDetections}
            disabled={loading}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors ${
              darkMode
                ? 'bg-gray-800 text-gray-300 hover:bg-gray-700'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            } ${loading ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
        <p className={`mt-2 text-sm ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
          Companies added to fund portfolios without press releases
        </p>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex-1 flex items-center justify-center py-20">
          <div className="flex flex-col items-center gap-2">
            <div className={`w-8 h-8 border-2 rounded-full animate-spin ${
              darkMode
                ? 'border-gray-600 border-t-gray-300'
                : 'border-gray-300 border-t-gray-600'
            }`} />
            <div className={darkMode ? 'text-gray-400' : 'text-gray-500'}>Loading...</div>
          </div>
        </div>
      ) : error ? (
        <div className="flex-1 flex items-center justify-center py-20">
          <div className={`text-center ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
            <AlertCircle size={48} className="mx-auto mb-4 opacity-50" />
            <p>{error}</p>
            <button
              onClick={loadDetections}
              className={`mt-4 text-sm underline ${
                darkMode ? 'text-blue-400' : 'text-blue-600'
              }`}
            >
              Try again
            </button>
          </div>
        </div>
      ) : detections.length === 0 ? (
        <div className="flex-1 flex items-center justify-center py-20">
          <div className={`text-center ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
            <Ghost size={48} className="mx-auto mb-4 opacity-50" />
            <p className="font-medium">No stealth detections yet</p>
            <p className="text-sm mt-1">Portfolio monitoring will detect new additions automatically</p>
          </div>
        </div>
      ) : (
        <div className={`divide-y ${darkMode ? 'divide-gray-700' : 'divide-border-light'}`}>
          {detections.map((detection) => (
            <div
              key={detection.id}
              className={`px-6 py-4 ${darkMode ? 'hover:bg-gray-800' : 'hover:bg-gray-50'}`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3">
                    <span className={`font-medium ${darkMode ? 'text-white' : 'text-gray-900'}`}>
                      {detection.company_name || 'Unknown Company'}
                    </span>
                    {detection.is_confirmed ? (
                      <span className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs ${
                        darkMode
                          ? 'bg-green-900 text-green-300'
                          : 'bg-green-100 text-green-700'
                      }`}>
                        <CheckCircle size={12} />
                        Confirmed
                      </span>
                    ) : (
                      <span className={`px-2 py-0.5 rounded-full text-xs ${
                        darkMode
                          ? 'bg-amber-900 text-amber-300'
                          : 'bg-amber-100 text-amber-700'
                      }`}>
                        Pending
                      </span>
                    )}
                  </div>
                  <div className={`mt-1 text-sm ${darkMode ? 'text-gray-400' : 'text-gray-500'}`}>
                    <span className="font-medium">{formatFundName(detection.fund_slug)}</span>
                    <span className="mx-2">·</span>
                    <span>{formatDate(detection.detected_at)}</span>
                  </div>
                  {detection.notes && (
                    <p className={`mt-2 text-sm ${darkMode ? 'text-gray-400' : 'text-gray-600'}`}>
                      {detection.notes}
                    </p>
                  )}
                </div>
                <a
                  href={detection.detected_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`flex items-center gap-1 px-3 py-1.5 rounded text-sm transition-colors ${
                    darkMode
                      ? 'text-blue-400 hover:bg-gray-800'
                      : 'text-blue-600 hover:bg-blue-50'
                  }`}
                >
                  <ExternalLink size={14} />
                  View
                </a>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
