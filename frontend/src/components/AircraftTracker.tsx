/**
 * AircraftTracker - Interactive aircraft tracking map
 * Real-time ADS-B data from OpenSky Network via backend
 * Smooth CSS-based position animation
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { X, Plane, Radio, Maximize2, Minimize2 } from 'lucide-react';
import { MapContainer, TileLayer, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

interface Aircraft {
  id: string;
  icao24: string;
  callsign?: string;
  origin_country?: string;
  latitude: number;
  longitude: number;
  altitude?: number;
  velocity?: number;
  heading?: number;
  vertical_rate?: number;
  on_ground?: boolean;
  category?: string;
  last_seen?: string;
}

interface AircraftTrackerProps {
  onClose: () => void;
}

// Backend URL - use deployed backend
const API_BASE = 'https://bud-tracker-backend-production.up.railway.app';
const WS_URL = 'wss://bud-tracker-backend-production.up.railway.app/aircraft/ws';

// USA center for initial map view
const USA_CENTER: [number, number] = [39.8283, -98.5795];
const DEFAULT_ZOOM = 4;

// Constants for position interpolation
const METERS_PER_DEGREE_LAT = 111320;
const DEG_TO_RAD = Math.PI / 180;

// Calculate predicted position based on velocity and heading
function predictPosition(
  lat: number,
  lon: number,
  velocity: number,
  heading: number,
  seconds: number
): [number, number] {
  if (!velocity || velocity < 10) return [lat, lon];

  const headingRad = heading * DEG_TO_RAD;
  const distance = velocity * seconds;

  const deltaLat = (distance * Math.cos(headingRad)) / METERS_PER_DEGREE_LAT;
  const metersPerDegreeLon = METERS_PER_DEGREE_LAT * Math.cos(lat * DEG_TO_RAD);
  const deltaLon = (distance * Math.sin(headingRad)) / metersPerDegreeLon;

  return [lat + deltaLat, lon + deltaLon];
}

// Get color based on altitude
function getAltitudeColor(altitude: number | undefined, onGround: boolean | undefined): string {
  if (onGround) return '#666666';
  const alt = altitude || 0;
  if (alt > 10000) return '#00ff9d';
  if (alt > 5000) return '#00cc7d';
  if (alt > 1000) return '#ffa500';
  return '#ff4444';
}

// Create aircraft icon HTML
function createIconHtml(color: string, heading: number): string {
  return `
    <div class="aircraft-icon" style="transform: rotate(${heading}deg);">
      <svg width="24" height="24" viewBox="0 0 32 32" fill="none">
        <path d="M16 4 L17.5 12 L26 14 L26 15.5 L17.5 14.5 L17.5 23 L21 25 L21 26.5 L16 25 L11 26.5 L11 25 L14.5 23 L14.5 14.5 L6 15.5 L6 14 L14.5 12 Z"
              fill="${color}" stroke="#0a0e14" stroke-width="0.5"/>
      </svg>
    </div>
  `;
}

// Format altitude in feet
function formatAltitude(meters?: number): string {
  if (!meters) return 'Ground';
  const feet = meters * 3.28084;
  return `${Math.round(feet).toLocaleString()} ft`;
}

// Format speed in knots
function formatSpeed(ms?: number): string {
  if (!ms) return '-';
  const knots = ms * 1.94384;
  return `${Math.round(knots)} kts`;
}

// Create popup content
function createPopupContent(ac: Aircraft): string {
  const altColor = ac.on_ground ? '#64748b' : '#00ff9d';
  return `
    <div style="background: #0d1117; color: white; padding: 12px; min-width: 200px; margin: -14px -20px; font-family: monospace; font-size: 11px;">
      <div style="color: #00ff9d; font-weight: bold; font-size: 13px; margin-bottom: 8px; border-bottom: 1px solid #21262d; padding-bottom: 8px;">
        ${ac.callsign?.trim() || ac.icao24}
      </div>
      <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
        <span style="color: #64748b;">ICAO:</span>
        <span>${ac.icao24}</span>
      </div>
      <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
        <span style="color: #64748b;">Altitude:</span>
        <span style="color: ${altColor};">${formatAltitude(ac.altitude)}</span>
      </div>
      <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
        <span style="color: #64748b;">Speed:</span>
        <span>${formatSpeed(ac.velocity)}</span>
      </div>
      <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
        <span style="color: #64748b;">Heading:</span>
        <span>${ac.heading ? `${Math.round(ac.heading)}°` : '-'}</span>
      </div>
      ${ac.origin_country ? `
      <div style="display: flex; justify-content: space-between;">
        <span style="color: #64748b;">Origin:</span>
        <span>${ac.origin_country}</span>
      </div>
      ` : ''}
    </div>
  `;
}

// Map controller for programmatic control and aircraft layer management
function AircraftLayer({
  aircraft,
  onAircraftClick
}: {
  aircraft: Aircraft[];
  onAircraftClick: (ac: Aircraft) => void;
}) {
  const map = useMap();
  const markersRef = useRef<Map<string, L.Marker>>(new Map());
  const animationRef = useRef<number | null>(null);
  const baseDataRef = useRef<Map<string, { ac: Aircraft; baseTime: number }>>(new Map());

  // Update base data when aircraft data changes
  useEffect(() => {
    const now = Date.now();
    const newBaseData = new Map<string, { ac: Aircraft; baseTime: number }>();

    aircraft.forEach(ac => {
      newBaseData.set(ac.id, { ac, baseTime: now });
    });

    baseDataRef.current = newBaseData;
  }, [aircraft]);

  // Create/update/remove markers
  useEffect(() => {
    const currentIds = new Set(aircraft.map(ac => ac.id));
    const existingIds = new Set(markersRef.current.keys());

    // Remove markers for aircraft no longer present
    existingIds.forEach(id => {
      if (!currentIds.has(id)) {
        const marker = markersRef.current.get(id);
        if (marker) {
          marker.remove();
          markersRef.current.delete(id);
        }
      }
    });

    // Add or update markers
    aircraft.forEach(ac => {
      const color = getAltitudeColor(ac.altitude, ac.on_ground);
      const heading = ac.heading || 0;

      if (markersRef.current.has(ac.id)) {
        // Update existing marker
        const marker = markersRef.current.get(ac.id)!;
        const icon = L.divIcon({
          className: 'aircraft-marker-animated',
          html: createIconHtml(color, heading),
          iconSize: [24, 24],
          iconAnchor: [12, 12],
        });
        marker.setIcon(icon);
        marker.setPopupContent(createPopupContent(ac));
      } else {
        // Create new marker
        const icon = L.divIcon({
          className: 'aircraft-marker-animated',
          html: createIconHtml(color, heading),
          iconSize: [24, 24],
          iconAnchor: [12, 12],
        });

        const marker = L.marker([ac.latitude, ac.longitude], { icon })
          .addTo(map)
          .bindPopup(createPopupContent(ac), {
            className: 'aircraft-popup-custom',
          });

        marker.on('click', () => onAircraftClick(ac));
        markersRef.current.set(ac.id, marker);
      }
    });
  }, [aircraft, map, onAircraftClick]);

  // Animation loop for smooth movement
  useEffect(() => {
    let isRunning = true;

    const animate = () => {
      if (!isRunning) return;

      const now = Date.now();

      baseDataRef.current.forEach(({ ac, baseTime }, id) => {
        const marker = markersRef.current.get(id);
        if (!marker) return;

        // Skip ground aircraft
        if (ac.on_ground || !ac.velocity || ac.velocity < 10) return;

        const elapsed = (now - baseTime) / 1000;
        // Clamp to 15 seconds max
        const clampedElapsed = Math.min(elapsed, 15);

        const [newLat, newLon] = predictPosition(
          ac.latitude,
          ac.longitude,
          ac.velocity || 0,
          ac.heading || 0,
          clampedElapsed
        );

        marker.setLatLng([newLat, newLon]);
      });

      animationRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      isRunning = false;
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      markersRef.current.forEach(marker => marker.remove());
      markersRef.current.clear();
    };
  }, []);

  return null;
}

// Map controller for flying to locations
function MapController({ center }: { center?: [number, number] }) {
  const map = useMap();

  useEffect(() => {
    if (center) {
      map.flyTo(center, 8, { duration: 1 });
    }
  }, [center, map]);

  return null;
}

export function AircraftTracker({ onClose }: AircraftTrackerProps) {
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [mapCenter, setMapCenter] = useState<[number, number] | undefined>();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch initial aircraft data
  const fetchAircraft = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/aircraft`);
      if (response.ok) {
        const data = await response.json();
        setAircraft(data.aircraft || []);
        setLoading(false);
      }
    } catch (error) {
      console.error('[AircraftTracker] Fetch error:', error);
      setLoading(false);
    }
  }, []);

  // Connect to WebSocket for real-time updates
  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('[AircraftTracker] WebSocket connected');
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'aircraft_update' && message.data) {
            setAircraft(message.data);
            setLoading(false);
          }
        } catch (e) {
          console.error('[AircraftTracker] Parse error:', e);
        }
      };

      ws.onclose = () => {
        console.log('[AircraftTracker] WebSocket disconnected');
        setConnected(false);
        reconnectTimeoutRef.current = setTimeout(connectWebSocket, 5000);
      };

      ws.onerror = (error) => {
        console.error('[AircraftTracker] WebSocket error:', error);
      };
    } catch (error) {
      console.error('[AircraftTracker] Failed to connect:', error);
      reconnectTimeoutRef.current = setTimeout(fetchAircraft, 10000);
    }
  }, [fetchAircraft]);

  // Initialize on mount
  useEffect(() => {
    fetchAircraft();
    connectWebSocket();

    const pollInterval = setInterval(fetchAircraft, 15000);

    return () => {
      clearInterval(pollInterval);
      if (wsRef.current) {
        wsRef.current.close();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [fetchAircraft, connectWebSocket]);

  // Handle aircraft selection
  const handleAircraftClick = useCallback((ac: Aircraft) => {
    setMapCenter([ac.latitude, ac.longitude]);
  }, []);

  // Count airborne vs ground
  const airborneCount = aircraft.filter((ac) => !ac.on_ground).length;
  const groundCount = aircraft.length - airborneCount;

  return (
    <div
      className={`fixed z-50 flex flex-col bg-[#0a0e14] border border-[#00ff9d]/30 shadow-2xl shadow-[#00ff9d]/10 ${
        isFullscreen
          ? 'inset-0'
          : 'inset-4 md:inset-8 lg:top-16 lg:bottom-16 lg:left-20 lg:right-20'
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d] bg-[#0d1117] shrink-0">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Plane className="w-5 h-5 text-[#00ff9d]" />
            <h2 className="text-sm font-bold text-[#00ff9d] uppercase tracking-widest">
              Carya Flight Tracker
            </h2>
          </div>
          <div className="flex items-center gap-3 text-xs">
            <div className="flex items-center gap-2">
              <div
                className={`w-2 h-2 rounded-full ${
                  connected ? 'bg-[#00ff9d] animate-pulse' : 'bg-amber-500'
                }`}
              />
              <span className="text-slate-400">
                {connected ? 'LIVE' : 'POLLING'}
              </span>
            </div>
            <span className="text-slate-600">|</span>
            <div className="flex items-center gap-1 text-slate-400">
              <Radio className="w-3 h-3" />
              <span>{aircraft.length.toLocaleString()} tracks</span>
            </div>
            <span className="text-slate-600">|</span>
            <span className="text-[#00ff9d]">{airborneCount.toLocaleString()} airborne</span>
            <span className="text-slate-600">|</span>
            <span className="text-slate-500">{groundCount.toLocaleString()} ground</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsFullscreen(!isFullscreen)}
            className="p-2 hover:bg-slate-800 text-slate-400 hover:text-white rounded transition-colors"
            title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          >
            {isFullscreen ? (
              <Minimize2 className="w-4 h-4" />
            ) : (
              <Maximize2 className="w-4 h-4" />
            )}
          </button>
          <button
            onClick={onClose}
            className="p-2 hover:bg-slate-800 text-slate-400 hover:text-white rounded transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Map Container */}
      <div className="flex-1 relative">
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center bg-[#0a0e14]">
            <div className="text-center">
              <Plane className="w-12 h-12 mx-auto mb-4 text-[#00ff9d] animate-pulse" />
              <p className="text-sm text-slate-400 uppercase tracking-widest">
                Loading aircraft data...
              </p>
            </div>
          </div>
        ) : (
          <MapContainer
            center={USA_CENTER}
            zoom={DEFAULT_ZOOM}
            style={{ height: '100%', width: '100%', background: '#0a0e14' }}
            zoomControl={true}
          >
            <TileLayer
              attribution='&copy; <a href="https://carto.com/">CARTO</a>'
              url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            />
            <MapController center={mapCenter} />
            <AircraftLayer
              aircraft={aircraft}
              onAircraftClick={handleAircraftClick}
            />
          </MapContainer>
        )}
      </div>

      {/* Footer */}
      <div className="px-4 py-2 border-t border-[#21262d] bg-[#0d1117] flex items-center justify-between text-[10px] text-slate-500 shrink-0">
        <div className="flex items-center gap-2">
          <span>Data: OpenSky Network</span>
          <span className="text-slate-700">|</span>
          <span>Coverage: Continental USA</span>
          <span className="text-slate-700">|</span>
          <span>Live Interpolation</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-[#00ff9d]" />
            <span>&gt;10k ft</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-amber-500" />
            <span>&lt;5k ft</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-red-500" />
            <span>&lt;1k ft</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-slate-500" />
            <span>Ground</span>
          </div>
        </div>
      </div>

      {/* Custom styles for Leaflet */}
      <style>{`
        .aircraft-marker-animated {
          background: transparent !important;
          border: none !important;
        }
        .aircraft-icon {
          filter: drop-shadow(0 0 4px rgba(0, 255, 157, 0.5));
          will-change: transform;
        }
        .aircraft-popup-custom .leaflet-popup-content-wrapper {
          background: transparent !important;
          border: none !important;
          border-radius: 0 !important;
          box-shadow: none !important;
          padding: 0 !important;
        }
        .aircraft-popup-custom .leaflet-popup-content {
          margin: 0 !important;
          background: #0d1117;
          border: 1px solid #00ff9d;
          box-shadow: 0 0 20px rgba(0, 255, 157, 0.3);
        }
        .aircraft-popup-custom .leaflet-popup-tip-container {
          display: none;
        }
        .leaflet-container {
          background: #0a0e14 !important;
          font-family: 'JetBrains Mono', monospace !important;
        }
        .leaflet-control-zoom a {
          background: #0d1117 !important;
          color: #00ff9d !important;
          border-color: #21262d !important;
        }
        .leaflet-control-zoom a:hover {
          background: #161b22 !important;
        }
        .leaflet-control-attribution {
          background: rgba(13, 17, 23, 0.8) !important;
          color: #7d8590 !important;
        }
        .leaflet-control-attribution a {
          color: #00ff9d !important;
        }
      `}</style>
    </div>
  );
}

export default AircraftTracker;
