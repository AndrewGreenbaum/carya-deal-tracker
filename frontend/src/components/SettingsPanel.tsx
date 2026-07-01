import { useRef, useEffect, useCallback } from 'react';

interface Node {
  x: number;
  y: number;
  baseX: number;
  baseY: number;
  vx: number;
  vy: number;
}

interface SettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export function SettingsPanel({ isOpen, onClose }: SettingsPanelProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mouseRef = useRef<{ x: number; y: number }>({ x: -9999, y: -9999 });
  const nodesRef = useRef<Node[]>([]);
  const edgesRef = useRef<[number, number][]>([]);
  const animRef = useRef<number>(0);
  const timeRef = useRef<number>(0);
  const sizeRef = useRef<{ w: number; h: number }>({ w: 0, h: 0 });

  const MOUSE_RADIUS = 140;
  const SPRING = 0.012;
  const DAMPING = 0.96;
  const BREAK_FORCE = 24;
  const SPACING = 38;

  const initWeb = useCallback(() => {
    const { w, h } = sizeRef.current;
    if (w === 0 || h === 0) return;

    const nodes: Node[] = [];
    const cols = Math.ceil(w / SPACING) + 2;
    const rows = Math.ceil(h / SPACING) + 2;

    // Dense jittered grid
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const x = col * SPACING + (Math.random() - 0.5) * SPACING * 0.4;
        const y = row * SPACING + (Math.random() - 0.5) * SPACING * 0.4;
        nodes.push({ x, y, baseX: x, baseY: y, vx: 0, vy: 0 });
      }
    }

    // Connect to neighbors (grid adjacency + diagonals)
    const edges: [number, number][] = [];
    const maxDist = SPACING * 1.6;

    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const idx = row * cols + col;
        // Right
        if (col < cols - 1) edges.push([idx, idx + 1]);
        // Down
        if (row < rows - 1) edges.push([idx, idx + cols]);
        // Diagonal down-right
        if (col < cols - 1 && row < rows - 1 && Math.random() > 0.3) {
          edges.push([idx, idx + cols + 1]);
        }
        // Diagonal down-left
        if (col > 0 && row < rows - 1 && Math.random() > 0.3) {
          edges.push([idx, idx + cols - 1]);
        }
        // Skip-one connections for web density
        if (col < cols - 2 && Math.random() > 0.7) {
          edges.push([idx, idx + 2]);
        }
        if (row < rows - 2 && Math.random() > 0.7) {
          edges.push([idx, idx + cols * 2]);
        }
      }
    }

    // Filter edges by actual distance
    const validEdges = edges.filter(([a, b]) => {
      const dx = nodes[a].baseX - nodes[b].baseX;
      const dy = nodes[a].baseY - nodes[b].baseY;
      return Math.sqrt(dx * dx + dy * dy) < maxDist;
    });

    nodesRef.current = nodes;
    edgesRef.current = validEdges;
  }, []);

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
      initWeb();
    };
    resize();
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, [isOpen, initWeb]);

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
      const nodes = nodesRef.current;
      const edges = edgesRef.current;

      // Subtle base oscillation
      const t = timeRef.current * 0.003;

      // Update physics
      for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i];

        // Gentle wave through base positions
        const wave = Math.sin(node.baseX * 0.008 + t) * 2 + Math.cos(node.baseY * 0.006 + t * 1.3) * 2;
        const targetX = node.baseX + wave;
        const targetY = node.baseY + Math.cos(node.baseX * 0.007 + t * 0.8) * 2;

        // Mouse repulsion
        const dx = node.x - mouse.x;
        const dy = node.y - mouse.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < MOUSE_RADIUS && dist > 0) {
          const force = ((1 - dist / MOUSE_RADIUS) ** 2) * BREAK_FORCE;
          node.vx += (dx / dist) * force;
          node.vy += (dy / dist) * force;
        }

        // Spring to base
        node.vx += (targetX - node.x) * SPRING;
        node.vy += (targetY - node.y) * SPRING;
        node.vx *= DAMPING;
        node.vy *= DAMPING;
        node.x += node.vx;
        node.y += node.vy;
      }

      // Draw edges
      for (let i = 0; i < edges.length; i++) {
        const [a, b] = edges[i];
        const na = nodes[a];
        const nb = nodes[b];
        const dx = na.x - nb.x;
        const dy = na.y - nb.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const baseDx = na.baseX - nb.baseX;
        const baseDy = na.baseY - nb.baseY;
        const baseDist = Math.sqrt(baseDx * baseDx + baseDy * baseDy) || 1;
        const stretch = dist / baseDist;

        if (stretch < 3.0) {
          const opacity = Math.max(0, 0.35 * (1 - (stretch - 1) * 0.5));
          ctx.beginPath();
          ctx.moveTo(na.x, na.y);
          ctx.lineTo(nb.x, nb.y);
          ctx.strokeStyle = `rgba(255, 255, 255, ${opacity})`;
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }
      }

      // Draw nodes
      for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i];
        const displacement = Math.sqrt((node.x - node.baseX) ** 2 + (node.y - node.baseY) ** 2);
        const intensity = Math.min(displacement / 40, 1);
        const radius = 0.8 + intensity * 1.5;
        const alpha = 0.2 + intensity * 0.8;
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${alpha})`;
        ctx.fill();
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
