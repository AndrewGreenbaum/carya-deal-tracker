import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { API_BASE, SESSION_EVENT, apiFetch, clearSession, getSession, signIn, signOut } from './session';
import './auth.css';

export default function AuthGate({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState(false);
  const [checking, setChecking] = useState(true);
  const [key, setKey] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    let expiryTimer: ReturnType<typeof setTimeout>;
    const changed = () => {
      const session = getSession();
      clearTimeout(expiryTimer);
      setAuthenticated(Boolean(session));
      if (session) expiryTimer = setTimeout(clearSession, Math.max(0, session.expires_at * 1000 - Date.now()));
    };
    window.addEventListener(SESSION_EVENT, changed);
    if (getSession()) {
      apiFetch(API_BASE + '/auth/session').then((response) => {
        if (active && response.ok) changed();
        else if (active) clearSession();
      }).catch(() => { if (active) clearSession(); })
        .finally(() => { if (active) setChecking(false); });
    } else setChecking(false);
    return () => { active = false; clearTimeout(expiryTimer); window.removeEventListener(SESSION_EVENT, changed); };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await signIn(key);
      setKey('');
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Unable to sign in.');
    } finally { setBusy(false); }
  }

  if (checking) return <div className="auth-page">Checking your session…</div>;
  if (!authenticated) return <main className="auth-page">
    <form className="auth-card" onSubmit={submit}>
      <h1>Carya Deal Tracker</h1>
      <p>Sign in with your private access key.</p>
      <label htmlFor="access-key">Access key</label>
      <input id="access-key" type="password" autoComplete="current-password" value={key}
        onChange={(event) => setKey(event.target.value)} required autoFocus />
      {error && <p className="auth-error" role="alert">{error}</p>}
      <button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      <small>Your session lasts one hour. Sign in again after a server restart. The access key is not saved in this browser.</small>
    </form>
  </main>;

  return <>{children}<button className="auth-signout" onClick={() => {
    void signOut().catch(() => { /* Local sign-out still completes if the server is unavailable. */ });
  }}>Sign out</button></>;
}
