export const API_BASE = (import.meta.env?.VITE_API_BASE || '/api').replace(/\/$/, '');
const STORAGE_KEY = 'carya-eagle-eye:session';
export const SESSION_EVENT = 'private-session-change';

export type Session = { access_token: string; expires_at: number };

export function clearSession(): void {
  sessionStorage.removeItem(STORAGE_KEY);
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export function getSession(): Session | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null') as Session | null;
    if (value && typeof value.access_token === 'string' && Number.isFinite(value.expires_at)
      && value.expires_at * 1000 > Date.now()) return value;
  } catch { /* A corrupt or expired session requires signing in again. */ }
  sessionStorage.removeItem(STORAGE_KEY);
  return null;
}

function apiUrl(url: string): URL {
  const target = new URL(url, window.location.origin);
  const base = new URL(API_BASE, window.location.origin);
  if (target.origin !== base.origin || (base.pathname !== '/' &&
      target.pathname !== base.pathname && !target.pathname.startsWith(base.pathname + '/'))) {
    throw new Error('Refusing to send a session to another service.');
  }
  if (target.protocol !== 'https:' && !['localhost', '127.0.0.1', '[::1]'].includes(target.hostname)) {
    throw new Error('A secure connection is required.');
  }
  return target;
}

export async function signIn(apiKey: string): Promise<Session> {
  const response = await fetch(apiUrl(API_BASE + '/auth/login'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
    cache: 'no-store',
    redirect: 'error',
  });
  if (!response.ok) {
    const message = response.status === 429 ? 'Too many attempts. Try again in a minute.'
      : response.status === 503 ? 'Access has not been configured on the server.'
      : 'Unable to sign in. Check your access key.';
    throw new Error(message);
  }
  const session = await response.json() as Session;
  if (typeof session.access_token !== 'string' || !Number.isFinite(session.expires_at)
      || session.expires_at * 1000 <= Date.now()) throw new Error('Invalid sign-in response.');
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  window.dispatchEvent(new Event(SESSION_EVENT));
  return session;
}

export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const session = getSession();
  if (!session) {
    clearSession();
    throw new Error('Please sign in again.');
  }
  const headers = new Headers(init.headers);
  headers.delete('X-API-Key');
  headers.set('Authorization', 'Bearer ' + session.access_token);
  const response = await fetch(apiUrl(url), { ...init, headers, cache: 'no-store', redirect: 'error' });
  if (response.status === 401) clearSession();
  return response;
}

export async function signOut(): Promise<void> {
  try {
    if (getSession()) await apiFetch(API_BASE + '/auth/logout', { method: 'POST' });
  } finally {
    clearSession();
  }
}

export async function downloadCsv(url: string, filename: string): Promise<void> {
  const response = await apiFetch(url);
  if (!response.ok) throw new Error('Unable to download the export. Please try again.');
  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

export function sessionSocket(path: string): WebSocket {
  const session = getSession();
  if (!session) throw new Error('Please sign in again.');
  const url = apiUrl(API_BASE + path);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return new WebSocket(url, ['carya-session', session.access_token]);
}
