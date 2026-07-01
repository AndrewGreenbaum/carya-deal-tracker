import { useRef, useEffect } from 'react';

interface SettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export function SettingsPanel({ isOpen, onClose }: SettingsPanelProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const timeRef = useRef<number>(0);
  const sizeRef = useRef<{ w: number; h: number }>({ w: 0, h: 0 });

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
    };
    resize();
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const CHAR_SIZE = 12;
    const CHARS = ' .,:-~=+*#%@█';

    const animate = () => {
      timeRef.current += 0.016;
      const t = timeRef.current;
      const { w, h } = sizeRef.current;
      ctx.fillStyle = '#000';
      ctx.fillRect(0, 0, w, h);

      const cols = Math.floor(w / (CHAR_SIZE * 0.6));
      const rows = Math.floor(h / CHAR_SIZE);
      const cx = w / 2;
      const cy = h / 2;

      // Two black holes spiraling inward
      // Orbit radius decreases over time, speed increases
      const maxRadius = Math.min(w, h) * 0.3;
      const orbitPhase = t * 1.2 + t * t * 0.08;
      const decay = Math.max(0, 1 - t * 0.04);
      const orbitRadius = maxRadius * decay;
      const mergeFlash = decay < 0.05 ? Math.min(1, (0.05 - decay) * 40) : 0;

      // After merge, restart slowly
      if (decay <= 0 && t > 26) {
        timeRef.current = 0;
      }

      const bh1x = cx + Math.cos(orbitPhase) * orbitRadius;
      const bh1y = cy + Math.sin(orbitPhase) * orbitRadius;
      const bh2x = cx + Math.cos(orbitPhase + Math.PI) * orbitRadius;
      const bh2y = cy + Math.sin(orbitPhase + Math.PI) * orbitRadius;

      // Gravitational wave rings
      const waveSpeed = 200;
      const waveCount = 6;

      ctx.font = `${CHAR_SIZE}px monospace`;
      ctx.textBaseline = 'top';

      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols; col++) {
          const px = col * CHAR_SIZE * 0.6 + CHAR_SIZE * 0.3;
          const py = row * CHAR_SIZE;

          // Distance to each black hole
          const dx1 = px - bh1x;
          const dy1 = py - bh1y;
          const dist1 = Math.sqrt(dx1 * dx1 + dy1 * dy1);

          const dx2 = px - bh2x;
          const dy2 = py - bh2y;
          const dist2 = Math.sqrt(dx2 * dx2 + dy2 * dy2);

          // Gravitational field intensity
          const eventHorizon = 25 + mergeFlash * 40;
          const g1 = dist1 < eventHorizon ? 1 : Math.min(1, (eventHorizon * 4) / dist1);
          const g2 = dist2 < eventHorizon ? 1 : Math.min(1, (eventHorizon * 4) / dist2);

          // Accretion disk (ring of bright chars around each BH)
          const diskRadius1 = 50 + Math.sin(t * 3) * 8;
          const diskRadius2 = 50 + Math.cos(t * 3) * 8;
          const disk1 = Math.exp(-((dist1 - diskRadius1) ** 2) / 400);
          const disk2 = Math.exp(-((dist2 - diskRadius2) ** 2) / 400);

          // Accretion swirl
          const angle1 = Math.atan2(dy1, dx1);
          const swirl1 = Math.sin(angle1 * 3 - t * 4 + dist1 * 0.05) * 0.5 + 0.5;
          const angle2 = Math.atan2(dy2, dx2);
          const swirl2 = Math.sin(angle2 * 3 + t * 4 - dist2 * 0.05) * 0.5 + 0.5;

          // Gravitational waves (expanding rings from center)
          let waveIntensity = 0;
          if (decay < 0.5) {
            const distCenter = Math.sqrt((px - cx) ** 2 + (py - cy) ** 2);
            for (let wv = 0; wv < waveCount; wv++) {
              const waveRadius = ((1 - decay) * waveSpeed * (wv + 1) * 0.5) % (Math.max(w, h) * 0.8);
              const waveDist = Math.abs(distCenter - waveRadius);
              if (waveDist < 15) {
                waveIntensity += (1 - waveDist / 15) * 0.4 * (1 - decay * 2);
              }
            }
          }

          // Combine intensities
          let intensity = 0;

          // Black hole cores (dark voids)
          if (dist1 < eventHorizon || dist2 < eventHorizon) {
            intensity = 0;
          } else {
            // Accretion disks
            intensity += disk1 * swirl1 * 0.9;
            intensity += disk2 * swirl2 * 0.9;
            // General gravitational glow
            intensity += g1 * 0.5;
            intensity += g2 * 0.5;
            // Gravitational waves
            intensity += waveIntensity;
            // Merge flash
            intensity += mergeFlash * 0.9;
            // Background star field
            const starHash = Math.sin(col * 127.1 + row * 311.7) * 43758.5453;
            const star = (starHash - Math.floor(starHash)) > 0.92 ? 0.2 : 0.03;
            intensity += star;
          }

          intensity = Math.min(1, Math.max(0, intensity));
          const charIdx = Math.floor(intensity * (CHARS.length - 1));
          const char = CHARS[charIdx];

          if (char !== ' ') {
            const brightness = Math.floor(60 + intensity * 195);
            ctx.fillStyle = `rgb(${brightness}, ${brightness}, ${brightness})`;
            ctx.fillText(char, col * CHAR_SIZE * 0.6, py);
          }
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
      className="fixed inset-0 z-50 bg-black"
      onClick={onClose}
    >
      <canvas ref={canvasRef} className="w-full h-full" />
    </div>
  );
}

export default SettingsPanel;
