/**
 * MindMapDocs.tsx - Sleek Visual Mind Map of System Documentation
 *
 * Modern, flowing design with curved connections and gradient nodes.
 * Access via /claude URL
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { RefreshCw, ArrowLeft, X } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE || 'https://bud-tracker-backend-production.up.railway.app';

// Node structure
interface MindMapNode {
  id: string;
  label: string;
  content?: string;
  children?: MindMapNode[];
  color: string;
  gradient: string;
}

// Curated mind map structure
function createMindMapStructure(sections: Map<string, string>): MindMapNode {
  return {
    id: 'root',
    label: 'Carya Eagle Eye',
    color: '#10b981',
    gradient: 'from-emerald-500 to-teal-600',
    children: [
      {
        id: 'pipeline',
        label: 'Data Pipeline',
        color: '#3b82f6',
        gradient: 'from-blue-500 to-indigo-600',
        children: [
          { id: 'scrapers', label: 'Scrapers', content: sections.get('data-sources'), color: '#8b5cf6', gradient: 'from-violet-500 to-purple-600' },
          { id: 'llm', label: 'LLM Extraction', content: sections.get('llm-pipeline'), color: '#8b5cf6', gradient: 'from-violet-500 to-purple-600' },
          { id: 'dedup', label: 'Deduplication', content: sections.get('deal-deduplication'), color: '#8b5cf6', gradient: 'from-violet-500 to-purple-600' },
          { id: 'scheduler', label: 'Scheduler', content: sections.get('scheduler-jobs-py-'), color: '#8b5cf6', gradient: 'from-violet-500 to-purple-600' },
        ]
      },
      {
        id: 'funds',
        label: '18 VC Funds',
        color: '#f59e0b',
        gradient: 'from-amber-500 to-orange-600',
        children: [
          { id: 'fund-list', label: 'Fund Registry', content: sections.get('the-18-tracked-funds'), color: '#f97316', gradient: 'from-orange-500 to-red-500' },
          { id: 'matching', label: 'Fund Matching', content: sections.get('fund-matching'), color: '#f97316', gradient: 'from-orange-500 to-red-500' },
          { id: 'brave', label: 'Brave Search', content: sections.get('brave-search-queries'), color: '#f97316', gradient: 'from-orange-500 to-red-500' },
          { id: 'gnews', label: 'Google News', content: sections.get('google-news-rss'), color: '#f97316', gradient: 'from-orange-500 to-red-500' },
        ]
      },
      {
        id: 'classify',
        label: 'Classification',
        color: '#ec4899',
        gradient: 'from-pink-500 to-rose-600',
        children: [
          { id: 'enterprise', label: 'Enterprise AI', content: sections.get('classification-rules'), color: '#f43f5e', gradient: 'from-rose-500 to-pink-600' },
          { id: 'lead', label: 'Lead Verification', content: sections.get('classification-rules'), color: '#f43f5e', gradient: 'from-rose-500 to-pink-600' },
          { id: 'validation', label: 'URL Validation', content: sections.get('url-validation-schemas-py-'), color: '#f43f5e', gradient: 'from-rose-500 to-pink-600' },
        ]
      },
      {
        id: 'enrich',
        label: 'Enrichment',
        color: '#06b6d4',
        gradient: 'from-cyan-500 to-blue-600',
        children: [
          { id: 'linkedin', label: 'LinkedIn', content: sections.get('linkedin-enrichment'), color: '#0ea5e9', gradient: 'from-sky-500 to-cyan-600' },
          { id: 'brave-client', label: 'Brave Client', content: sections.get('enrichment-system'), color: '#0ea5e9', gradient: 'from-sky-500 to-cyan-600' },
          { id: 'dates', label: 'Date Verification', content: sections.get('data-verification'), color: '#0ea5e9', gradient: 'from-sky-500 to-cyan-600' },
        ]
      },
      {
        id: 'ui',
        label: 'Frontend',
        color: '#84cc16',
        gradient: 'from-lime-500 to-green-600',
        children: [
          { id: 'dashboard', label: 'Dashboard', content: sections.get('frontend-dashboard'), color: '#22c55e', gradient: 'from-green-500 to-emerald-600' },
          { id: 'api', label: 'API Endpoints', content: sections.get('key-api-endpoints'), color: '#22c55e', gradient: 'from-green-500 to-emerald-600' },
        ]
      },
      {
        id: 'infra',
        label: 'Infrastructure',
        color: '#6366f1',
        gradient: 'from-indigo-500 to-violet-600',
        children: [
          { id: 'db', label: 'Database', content: sections.get('database-schema'), color: '#a855f7', gradient: 'from-purple-500 to-indigo-600' },
          { id: 'deploy', label: 'Deployment', content: sections.get('github-railway-cli'), color: '#a855f7', gradient: 'from-purple-500 to-indigo-600' },
          { id: 'costs', label: 'API Costs', content: sections.get('api-costs'), color: '#a855f7', gradient: 'from-purple-500 to-indigo-600' },
        ]
      },
      {
        id: 'stealth',
        label: 'Stealth Detection',
        color: '#78716c',
        gradient: 'from-stone-500 to-zinc-600',
        children: [
          { id: 'portfolio', label: 'Portfolio Diff', content: sections.get('portfolio-diff-scraper'), color: '#a8a29e', gradient: 'from-stone-400 to-zinc-500' },
          { id: 'delaware', label: 'Delaware Corps', content: sections.get('delaware-scraper'), color: '#a8a29e', gradient: 'from-stone-400 to-zinc-500' },
          { id: 'sec', label: 'SEC EDGAR', content: sections.get('data-sources'), color: '#a8a29e', gradient: 'from-stone-400 to-zinc-500' },
        ]
      },
      {
        id: 'docs',
        label: 'Reference',
        color: '#14b8a6',
        gradient: 'from-teal-500 to-emerald-600',
        children: [
          { id: 'quick', label: 'Quick Reference', content: sections.get('quick-reference'), color: '#2dd4bf', gradient: 'from-teal-400 to-cyan-500' },
          { id: 'arch', label: 'Architecture', content: sections.get('architecture'), color: '#2dd4bf', gradient: 'from-teal-400 to-cyan-500' },
          { id: 'trouble', label: 'Troubleshooting', content: sections.get('troubleshooting'), color: '#2dd4bf', gradient: 'from-teal-400 to-cyan-500' },
        ]
      },
    ]
  };
}

// Parse markdown into sections map
function parseMarkdownSections(content: string): Map<string, string> {
  const sections = new Map<string, string>();
  const lines = content.split('\n');
  let currentSection = '';
  let currentContent: string[] = [];
  let inCodeBlock = false;

  for (const line of lines) {
    if (line.startsWith('```')) {
      inCodeBlock = !inCodeBlock;
      currentContent.push(line);
      continue;
    }

    if (inCodeBlock) {
      currentContent.push(line);
      continue;
    }

    const h2Match = line.match(/^## (.+)$/);
    if (h2Match) {
      if (currentSection) {
        sections.set(currentSection, currentContent.join('\n'));
      }
      currentSection = h2Match[1].toLowerCase().replace(/[^a-z0-9]+/g, '-');
      currentContent = [];
    } else {
      currentContent.push(line);
    }
  }

  if (currentSection) {
    sections.set(currentSection, currentContent.join('\n'));
  }

  return sections;
}

// Render markdown content
function ContentRenderer({ content }: { content: string }) {
  const lines = content.split('\n');
  const elements: JSX.Element[] = [];
  let inCodeBlock = false;
  let codeLines: string[] = [];
  let inTable = false;
  let tableRows: string[][] = [];

  const flushCode = () => {
    if (codeLines.length > 0) {
      elements.push(
        <pre key={elements.length} className="bg-black/40 backdrop-blur rounded-lg p-4 overflow-x-auto my-4 text-xs border border-white/5">
          <code className="text-emerald-300 font-mono">{codeLines.join('\n')}</code>
        </pre>
      );
      codeLines = [];
    }
  };

  const flushTable = () => {
    if (tableRows.length > 1) {
      const headers = tableRows[0];
      const body = tableRows.slice(2);
      elements.push(
        <div key={elements.length} className="overflow-x-auto my-4">
          <table className="min-w-full text-xs">
            <thead>
              <tr className="border-b border-white/10">
                {headers.map((h, i) => (
                  <th key={i} className="px-3 py-2 text-left text-slate-300 font-medium">
                    {h.trim()}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, i) => (
                <tr key={i} className="border-b border-white/5">
                  {row.map((cell, j) => (
                    <td key={j} className="px-3 py-2 text-slate-400">
                      {cell.trim()}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      tableRows = [];
    }
  };

  for (const line of lines) {
    if (line.startsWith('```')) {
      if (inCodeBlock) {
        flushCode();
        inCodeBlock = false;
      } else {
        flushTable();
        inCodeBlock = true;
      }
      continue;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    if (line.includes('|') && line.trim().startsWith('|')) {
      if (!inTable) inTable = true;
      tableRows.push(line.split('|').slice(1, -1));
      continue;
    } else if (inTable) {
      flushTable();
      inTable = false;
    }

    if (!line.trim()) continue;

    if (line.startsWith('### ')) {
      elements.push(
        <h4 key={elements.length} className="text-base font-semibold text-white mt-5 mb-2">
          {line.slice(4)}
        </h4>
      );
      continue;
    }

    if (line.match(/^[-*] /)) {
      elements.push(
        <li key={elements.length} className="text-slate-300 ml-4 my-1 text-sm list-disc">
          {line.slice(2)}
        </li>
      );
      continue;
    }

    elements.push(
      <p key={elements.length} className="text-slate-300 my-2 text-sm leading-relaxed">
        {line}
      </p>
    );
  }

  flushCode();
  flushTable();

  return <div className="space-y-1">{elements}</div>;
}

// Branch Node Component (category with children)
function BranchNode({
  node,
  isExpanded,
  onToggle,
  onChildClick,
  selectedChildId,
  index,
  total,
}: {
  node: MindMapNode;
  isExpanded: boolean;
  onToggle: () => void;
  onChildClick: (child: MindMapNode) => void;
  selectedChildId: string | null;
  index: number;
  total: number;
}) {
  // Calculate position based on index for organic feel
  const angle = (index / total) * Math.PI * 2 - Math.PI / 2;
  const radiusX = 320;
  const radiusY = 240;

  return (
    <div
      className="absolute transition-all duration-700 ease-out"
      style={{
        left: `calc(50% + ${Math.cos(angle) * radiusX}px)`,
        top: `calc(50% + ${Math.sin(angle) * radiusY}px)`,
        transform: 'translate(-50%, -50%)',
      }}
    >
      {/* Main branch node */}
      <button
        onClick={onToggle}
        className={`
          relative group px-5 py-3 rounded-2xl
          bg-gradient-to-br ${node.gradient}
          shadow-lg hover:shadow-xl
          transform hover:scale-105 transition-all duration-300
          border border-white/20
          ${isExpanded ? 'ring-2 ring-white/40 scale-105' : ''}
        `}
        style={{
          boxShadow: isExpanded
            ? `0 0 40px ${node.color}50, 0 10px 40px ${node.color}30`
            : `0 4px 20px ${node.color}20`,
        }}
      >
        <span className="text-white font-semibold text-sm whitespace-nowrap drop-shadow-lg">
          {node.label}
        </span>

        {/* Pulse effect when expanded */}
        {isExpanded && (
          <span
            className="absolute inset-0 rounded-2xl animate-ping opacity-20"
            style={{ backgroundColor: node.color }}
          />
        )}
      </button>

      {/* Children nodes - floating outward */}
      {isExpanded && node.children && (
        <div className="absolute left-1/2 top-1/2">
          {node.children.map((child, i) => {
            const childAngle = angle + ((i - (node.children!.length - 1) / 2) * 0.4);
            const childRadius = 140;
            const isSelected = selectedChildId === child.id;

            return (
              <button
                key={child.id}
                onClick={() => onChildClick(child)}
                className={`
                  absolute px-4 py-2 rounded-xl
                  bg-gradient-to-br ${child.gradient}
                  shadow-md hover:shadow-lg
                  transform hover:scale-110 transition-all duration-300
                  border border-white/10
                  ${isSelected ? 'ring-2 ring-white/50 scale-110' : ''}
                `}
                style={{
                  left: `${Math.cos(childAngle) * childRadius}px`,
                  top: `${Math.sin(childAngle) * childRadius}px`,
                  transform: 'translate(-50%, -50%)',
                  boxShadow: isSelected
                    ? `0 0 30px ${child.color}60`
                    : `0 4px 15px ${child.color}20`,
                  animationDelay: `${i * 50}ms`,
                }}
              >
                <span className="text-white text-xs font-medium whitespace-nowrap drop-shadow">
                  {child.label}
                </span>
              </button>
            );
          })}

          {/* Curved connection lines */}
          <svg
            className="absolute pointer-events-none"
            style={{
              left: '-200px',
              top: '-200px',
              width: '400px',
              height: '400px',
            }}
          >
            {node.children.map((child, i) => {
              const childAngle = angle + ((i - (node.children!.length - 1) / 2) * 0.4);
              const childRadius = 140;
              const endX = 200 + Math.cos(childAngle) * childRadius;
              const endY = 200 + Math.sin(childAngle) * childRadius;
              const midX = 200 + Math.cos(childAngle) * (childRadius * 0.5);
              const midY = 200 + Math.sin(childAngle) * (childRadius * 0.5);

              return (
                <path
                  key={child.id}
                  d={`M 200,200 Q ${midX},${midY} ${endX},${endY}`}
                  fill="none"
                  stroke={`${node.color}60`}
                  strokeWidth="2"
                  strokeLinecap="round"
                  className="animate-draw"
                  style={{
                    strokeDasharray: '200',
                    strokeDashoffset: '200',
                    animation: `draw 0.5s ease-out ${i * 0.1}s forwards`,
                  }}
                />
              );
            })}
          </svg>
        </div>
      )}
    </div>
  );
}

// Main component
export function MindMapDocs() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mindMap, setMindMap] = useState<MindMapNode | null>(null);
  const [expandedBranch, setExpandedBranch] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<MindMapNode | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fetchDocs = async () => {
      setLoading(true);
      try {
        const res = await fetch(`${API_BASE}/docs/claude`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const sections = parseMarkdownSections(data.content);
        setMindMap(createMindMapStructure(sections));
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load documentation');
      } finally {
        setLoading(false);
      }
    };
    fetchDocs();
  }, []);

  const handleBranchToggle = useCallback((branchId: string) => {
    setExpandedBranch(prev => prev === branchId ? null : branchId);
    setSelectedNode(null);
  }, []);

  const handleChildClick = useCallback((child: MindMapNode) => {
    setSelectedNode(child);
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-[#050506] flex items-center justify-center">
        <div className="relative">
          <RefreshCw className="w-10 h-10 text-emerald-400 animate-spin" />
          <div className="absolute inset-0 blur-xl bg-emerald-400/30 animate-pulse" />
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen bg-[#050506] text-slate-300 overflow-hidden flex">
      {/* Main Mind Map Area */}
      <div className="flex-1 relative" ref={containerRef}>
        {/* Ambient background effects */}
        <div className="absolute inset-0 overflow-hidden">
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-emerald-500/5 rounded-full blur-3xl" />
          <div className="absolute bottom-1/4 right-1/4 w-80 h-80 bg-blue-500/5 rounded-full blur-3xl" />
          <div className="absolute top-1/2 left-1/2 w-64 h-64 bg-purple-500/5 rounded-full blur-3xl -translate-x-1/2 -translate-y-1/2" />
        </div>

        {/* Header */}
        <header className="absolute top-0 left-0 right-0 z-50 px-6 py-4">
          <div className="flex items-center justify-between">
            <a
              href="/"
              className="flex items-center gap-2 text-slate-400 hover:text-white transition-colors bg-black/30 backdrop-blur-sm px-4 py-2 rounded-full border border-white/10"
            >
              <ArrowLeft className="w-4 h-4" />
              <span className="text-sm">Dashboard</span>
            </a>
            <div className="text-sm text-slate-500 bg-black/30 backdrop-blur-sm px-4 py-2 rounded-full border border-white/10">
              System Mind Map
            </div>
          </div>
        </header>

        {/* Mind Map Content */}
        {error ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="bg-red-900/20 border border-red-800 rounded-2xl p-6 text-red-400 backdrop-blur-sm">
              {error}
            </div>
          </div>
        ) : mindMap ? (
          <div className="absolute inset-0 flex items-center justify-center">
            {/* Central node */}
            <div className="relative">
              <button
                onClick={() => { setExpandedBranch(null); setSelectedNode(null); }}
                className={`
                  relative z-10 px-8 py-5 rounded-3xl
                  bg-gradient-to-br ${mindMap.gradient}
                  shadow-2xl hover:shadow-emerald-500/30
                  transform hover:scale-105 transition-all duration-500
                  border border-white/30
                `}
                style={{
                  boxShadow: '0 0 60px rgba(16, 185, 129, 0.3), 0 0 100px rgba(16, 185, 129, 0.1)',
                }}
              >
                <span className="text-white font-bold text-xl drop-shadow-lg">
                  {mindMap.label}
                </span>

                {/* Breathing glow */}
                <div
                  className="absolute inset-0 rounded-3xl animate-pulse"
                  style={{
                    boxShadow: '0 0 40px rgba(16, 185, 129, 0.4)',
                  }}
                />
              </button>

              {/* Branch nodes */}
              {mindMap.children?.map((branch, i) => (
                <BranchNode
                  key={branch.id}
                  node={branch}
                  isExpanded={expandedBranch === branch.id}
                  onToggle={() => handleBranchToggle(branch.id)}
                  onChildClick={handleChildClick}
                  selectedChildId={selectedNode?.id || null}
                  index={i}
                  total={mindMap.children!.length}
                />
              ))}

              {/* Connection lines from center to branches */}
              <svg
                className="absolute pointer-events-none"
                style={{
                  left: '50%',
                  top: '50%',
                  width: '800px',
                  height: '600px',
                  transform: 'translate(-50%, -50%)',
                }}
              >
                <defs>
                  <linearGradient id="line-gradient" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="#10b981" stopOpacity="0.6" />
                    <stop offset="100%" stopColor="#10b981" stopOpacity="0.1" />
                  </linearGradient>
                </defs>
                {mindMap.children?.map((branch, i) => {
                  const angle = (i / mindMap.children!.length) * Math.PI * 2 - Math.PI / 2;
                  const radiusX = 320;
                  const radiusY = 240;
                  const endX = 400 + Math.cos(angle) * radiusX;
                  const endY = 300 + Math.sin(angle) * radiusY;
                  const ctrlX = 400 + Math.cos(angle) * radiusX * 0.4;
                  const ctrlY = 300 + Math.sin(angle) * radiusY * 0.4;

                  return (
                    <path
                      key={branch.id}
                      d={`M 400,300 Q ${ctrlX},${ctrlY} ${endX},${endY}`}
                      fill="none"
                      stroke={expandedBranch === branch.id ? branch.color : '#334155'}
                      strokeWidth={expandedBranch === branch.id ? 3 : 1.5}
                      strokeOpacity={expandedBranch === branch.id ? 0.8 : 0.3}
                      strokeLinecap="round"
                      className="transition-all duration-500"
                    />
                  );
                })}
              </svg>
            </div>
          </div>
        ) : null}

        {/* Instructions hint */}
        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 text-xs text-slate-600 bg-black/30 backdrop-blur-sm px-4 py-2 rounded-full border border-white/5">
          Click branches to explore • Click items to view details
        </div>
      </div>

      {/* Detail Panel - Slides in from right */}
      <div
        className={`
          w-[500px] bg-black/40 backdrop-blur-xl border-l border-white/10
          transform transition-all duration-500 ease-out
          ${selectedNode ? 'translate-x-0' : 'translate-x-full'}
          flex flex-col
        `}
      >
        {selectedNode && (
          <>
            {/* Panel Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-white/10">
              <div className="flex items-center gap-3">
                <div
                  className={`w-3 h-3 rounded-full bg-gradient-to-br ${selectedNode.gradient}`}
                  style={{ boxShadow: `0 0 10px ${selectedNode.color}` }}
                />
                <h3 className="font-bold text-white text-lg">{selectedNode.label}</h3>
              </div>
              <button
                onClick={() => setSelectedNode(null)}
                className="p-2 hover:bg-white/10 rounded-lg transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Panel Content */}
            <div className="flex-1 overflow-y-auto p-6">
              {selectedNode.content ? (
                <ContentRenderer content={selectedNode.content} />
              ) : (
                <p className="text-slate-500 italic">No detailed content available</p>
              )}
            </div>
          </>
        )}
      </div>

      {/* CSS for line animation */}
      <style>{`
        @keyframes draw {
          to {
            stroke-dashoffset: 0;
          }
        }
      `}</style>
    </div>
  );
}

export default MindMapDocs;
