import { useRef, useEffect, useCallback } from 'react';

interface Node {
  x: number;
  y: number;
  baseX: number;
  baseY: number;
  vx: number;
  vy: number;
}

interface Helix {
  nodes: Node[][];
  cx: number;
  cy: number;
  angle: number;
  length: number;
  amplitude: number;
  speed: number;
  nodeCount: number;
}

interface Shard {
  nodes: Node[];
  edges: [number, number][];
}

interface SettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export function SettingsPanel({ isOpen, onClose }: SettingsPanelProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mouseRef = useRef<{ x: number; y: number }>({ x: -9999, y: -9999 });
  const helicesRef = useRef<Helix[]>([]);
  const shardsRef = useRef<Shard[]>([]);
  const animRef = useRef<number>(0);
  const timeRef = useRef<number>(0);
  const sizeRef = useRef<{ w: number; h: number }>({ w: 0, h: 0 });

  const MOUSE_RADIUS = 160;
  const SPRING = 0.014;
  const DAMPING = 0.96;
  const BREAK_FORCE = 22;

  const createHelix = useCallback((cx: number, cy: number, length: number, amplitude: number, angle: number, speed: number, nodeCount: number): Helix => {
    const strands: Node[][] = [[], []];
    const spacing = length / (nodeCount - 1);
    const cosA = Math.cos(angle);
    const sinA = Math.sin(angle);
    const perpX = -sinA;
    const perpY = cosA;

    for (let i = 0; i < nodeCount; i++) {
      const t = i * spacing - length / 2;
      const phase1 = (i / nodeCount) * Math.PI * 6;
      const phase2 = phase1 + Math.PI;
      const axisX = cx + t * cosA;
      const axisY = cy + t * sinA;
      const offset1 = Math.sin(phase1) * amplitude;
      const offset2 = Math.sin(phase2) * amplitude;

      strands[0].push({
        x: axisX + perpX * offset1, y: axisY + perpY * offset1,
        baseX: axisX + perpX * offset1, baseY: axisY + perpY * offset1,
        vx: 0, vy: 0,
      });
      strands[1].push({
        x: axisX + perpX * offset2, y: axisY + perpY * offset2,
        baseX: axisX + perpX * offset2, baseY: axisY + perpY * offset2,
        vx: 0, vy: 0,
      });
    }
    return { nodes: strands, cx, cy, angle, length, amplitude, speed, nodeCount };
  }, []);

  const createShard = useCallback((cx: number, cy: number, size: number, vertices: number): Shard => {
    const nodes: Node[] = [];
    const edges: [number, number][] = [];

    // Create vertices in a polygon + center
    nodes.push({ x: cx, y: cy, baseX: cx, baseY: cy, vx: 0, vy: 0 });
    for (let i = 0; i < vertices; i++) {
      const angle = (i / vertices) * Math.PI * 2 + (Math.random() - 0.5) * 0.5;
      const r = size * (0.6 + Math.random() * 0.4);
      const x = cx + Math.cos(angle) * r;
      const y = cy + Math.sin(angle) * r;
      nodes.push({ x, y, baseX: x, baseY: y, vx: 0, vy: 0 });
    }

    // Connect center to all vertices
    for (let i = 1; i <= vertices; i++) {
      edges.push([0, i]);
    }
    // Connect adjacent vertices
    for (let i = 1; i <= vertices; i++) {
      edges.push([i, i < vertices ? i + 1 : 1]);
    }
    // Add some internal cross-braces for complexity
    if (vertices >= 5) {
      for (let i = 1; i <= vertices; i++) {
        const skip = Math.floor(vertices / 2);
        const target = ((i - 1 + skip) % vertices) + 1;
        if (Math.random() > 0.4) edges.push([i, target]);
      }
    }

    return { nodes, edges };
  }, []);

  const initStructures = useCallback(() => {
    const { w, h } = sizeRef.current;
    if (w === 0 || h === 0) return;

    const helices: Helix[] = [];
    const shards: Shard[] = [];

    // Many helices of varying sizes
    const helixCount = Math.max(8, Math.floor((w * h) / 80000));
    for (let i = 0; i < helixCount; i++) {
      const cx = Math.random() * w;
      const cy = Math.random() * h;
      const length = 80 + Math.random() * (h * 0.6);
      const amplitude = 15 + Math.random() * 45;
      const angle = Math.random() * Math.PI * 2;
      const speed = 0.002 + Math.random() * 0.005;
      const nodeCount = Math.floor(16 + Math.random() * 28);
      helices.push(createHelix(cx, cy, length, amplitude, angle, speed, nodeCount));
    }

    // Crystal shards scattered everywhere
    const shardCount = Math.max(15, Math.floor((w * h) / 50000));
    for (let i = 0; i < shardCount; i++) {
      const cx = Math.random() * w;
      const cy = Math.random() * h;
      const size = 20 + Math.random() * 80;
      const vertices = 3 + Math.floor(Math.random() * 5); // 3-7 sided
      shards.push(createShard(cx, cy, size, vertices));
    }

    helicesRef.current = helices;
    shardsRef.current = shards;
  }, [createHelix, createShard]);

  useEffect(() => {
    if (!isOpen) return;
    const resize = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      sizeRef.current = { w, h };
      if (canvasRef.current) {
        canvasRef.current.width = w;
        canvasRef.current.height = h;
      }
      initStructures();
    };
    resize();
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, [isOpen, initStructures]);

  useEffect(() => {
    if (!isOpen) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const animate = () => {
      timeRef.current += 1;
      const { w, h } = sizeRef.current;
      ctx.clearRect(0, 0, w, h);
      const mouse = mouseRef.current;

      // Update and draw helices
      for (const helix of helicesRef.current) {
        const { nodes, cx, cy, angle, length, amplitude, speed, nodeCount } = helix;
        const cosA = Math.cos(angle);
        const sinA = Math.sin(angle);
        const perpX = -sinA;
        const perpY = cosA;

        for (let s = 0; s < 2; s++) {
          for (let i = 0; i < nodes[s].length; i++) {
            const node = nodes[s][i];
            const t = (i / (nodeCount - 1)) * length - length / 2;
            const phase = ((i / nodeCount) * Math.PI * 6) + timeRef.current * speed + (s * Math.PI);
            const offset = Math.sin(phase) * amplitude;
            const axisX = cx + t * cosA;
            const axisY = cy + t * sinA;
            node.baseX = axisX + perpX * offset;
            node.baseY = axisY + perpY * offset;

            const dx = node.x - mouse.x;
            const dy = node.y - mouse.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < MOUSE_RADIUS && dist > 0) {
              const force = ((1 - dist / MOUSE_RADIUS) ** 2) * BREAK_FORCE;
              node.vx += (dx / dist) * force;
              node.vy += (dy / dist) * force;
            }
            node.vx += (node.baseX - node.x) * SPRING;
            node.vy += (node.baseY - node.y) * SPRING;
            node.vx *= DAMPING;
            node.vy *= DAMPING;
            node.x += node.vx;
            node.y += node.vy;
          }
        }

        // Rungs
        for (let i = 0; i < nodeCount; i++) {
          const a = nodes[0][i];
          const b = nodes[1][i];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const baseDx = a.baseX - b.baseX;
          const baseDy = a.baseY - b.baseY;
          const baseDist = Math.sqrt(baseDx * baseDx + baseDy * baseDy) || 1;
          const stretch = dist / baseDist;
          if (stretch < 3.0) {
            const opacity = Math.max(0, 0.3 * (1 - (stretch - 1) * 0.5));
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.strokeStyle = `rgba(255, 255, 255, ${opacity})`;
            ctx.lineWidth = 0.4;
            ctx.stroke();
          }
        }

        // Strands
        for (let s = 0; s < 2; s++) {
          const strand = nodes[s];
          ctx.beginPath();
          ctx.moveTo(strand[0].x, strand[0].y);
          for (let i = 1; i < strand.length; i++) {
            const prev = strand[i - 1];
            const curr = strand[i];
            ctx.quadraticCurveTo(prev.x, prev.y, (prev.x + curr.x) / 2, (prev.y + curr.y) / 2);
          }
          ctx.strokeStyle = 'rgba(255, 255, 255, 0.55)';
          ctx.lineWidth = 1;
          ctx.stroke();
        }

        // Nodes
        for (let s = 0; s < 2; s++) {
          for (let i = 0; i < nodes[s].length; i++) {
            const node = nodes[s][i];
            const displacement = Math.sqrt((node.x - node.baseX) ** 2 + (node.y - node.baseY) ** 2);
            const intensity = Math.min(displacement / 50, 1);
            ctx.beginPath();
            ctx.arc(node.x, node.y, 1.2 + intensity * 1.8, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(255, 255, 255, ${0.3 + intensity * 0.7})`;
            ctx.fill();
          }
        }
      }

      // Update and draw shards
      for (const shard of shardsRef.current) {
        for (const node of shard.nodes) {
          const dx = node.x - mouse.x;
          const dy = node.y - mouse.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < MOUSE_RADIUS && dist > 0) {
            const force = ((1 - dist / MOUSE_RADIUS) ** 2) * BREAK_FORCE * 1.4;
            node.vx += (dx / dist) * force;
            node.vy += (dy / dist) * force;
          }
          node.vx += (node.baseX - node.x) * SPRING * 0.8;
          node.vy += (node.baseY - node.y) * SPRING * 0.8;
          node.vx *= DAMPING;
          node.vy *= DAMPING;
          node.x += node.vx;
          node.y += node.vy;
        }

        // Draw edges
        for (const [a, b] of shard.edges) {
          const na = shard.nodes[a];
          const nb = shard.nodes[b];
          const dx = na.x - nb.x;
          const dy = na.y - nb.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const baseDx = na.baseX - nb.baseX;
          const baseDy = na.baseY - nb.baseY;
          const baseDist = Math.sqrt(baseDx * baseDx + baseDy * baseDy) || 1;
          const stretch = dist / baseDist;

          if (stretch < 3.5) {
            const opacity = Math.max(0, 0.4 * (1 - (stretch - 1) * 0.4));
            ctx.beginPath();
            ctx.moveTo(na.x, na.y);
            ctx.lineTo(nb.x, nb.y);
            ctx.strokeStyle = `rgba(255, 255, 255, ${opacity})`;
            ctx.lineWidth = 0.6;
            ctx.stroke();
          }
        }

        // Draw shard vertices
        for (const node of shard.nodes) {
          const displacement = Math.sqrt((node.x - node.baseX) ** 2 + (node.y - node.baseY) ** 2);
          const intensity = Math.min(displacement / 50, 1);
          ctx.beginPath();
          ctx.arc(node.x, node.y, 1 + intensity * 1.5, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(255, 255, 255, ${0.25 + intensity * 0.75})`;
          ctx.fill();
        }
      }

      animRef.current = requestAnimationFrame(animate);
    };

    animRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(animRef.current);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black cursor-crosshair"
      onMouseMove={(e) => { mouseRef.current = { x: e.clientX, y: e.clientY }; }}
      onMouseLeave={() => { mouseRef.current = { x: -9999, y: -9999 }; }}
      onClick={onClose}
    >
      <canvas ref={canvasRef} className="w-full h-full" />
    </div>
  );
}

export default SettingsPanel;
