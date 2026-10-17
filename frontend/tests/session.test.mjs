import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { apiFetch, signIn, signOut, getSession, downloadCsv } from '../src/auth/session.ts';

const values = new Map();
const events = [];
const calls = [];
beforeEach(() => {
  values.clear(); events.length = 0; calls.length = 0;
  globalThis.sessionStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  globalThis.window = { location: { origin: 'http://localhost:3000' }, dispatchEvent: (event) => events.push(event.type) };
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    if (String(url).endsWith('/auth/login')) {
      return Response.json({ access_token: 'synthetic-session', expires_at: Math.floor(Date.now() / 1000) + 3600 });
    }
    return Response.json({ ok: true });
  };
});

test('sign-in retains the expiring session, never the private access key', async () => {
  await signIn('private-key-used-only-for-this-test');
  assert.equal(getSession().access_token, 'synthetic-session');
  assert.equal([...values.values()].some((value) => value.includes('private-key')), false);
  await apiFetch('/api/profiles', { headers: { 'X-API-Key': 'obsolete' } });
  assert.equal(calls[1].init.headers.get('Authorization'), 'Bearer synthetic-session');
  assert.equal(calls[1].init.headers.has('X-API-Key'), false);
  assert.equal(calls[1].url.includes('synthetic-session'), false);
  assert.equal(calls[1].init.redirect, 'error');
});

test('credentials are not sent to another host or through a redirect', async () => {
  await signIn('private-key');
  await assert.rejects(apiFetch('https://other.example/private'), /another service/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.redirect, 'error');
});

test('expired and rejected sessions return the user to sign-in', async () => {
  await signIn('private-key');
  const [storageKey] = values.keys();
  values.set(storageKey, JSON.stringify({ access_token: 'expired', expires_at: 1 }));
  await assert.rejects(apiFetch('/api/profiles'), /sign in again/);
  assert.equal(getSession(), null);
  await signIn('private-key');
  globalThis.fetch = async () => new Response('', { status: 401 });
  await apiFetch('/api/profiles');
  assert.equal(getSession(), null);
});

test('sign-out clears local credentials even if the server is unavailable', async () => {
  await signIn('private-key');
  globalThis.fetch = async () => { throw new Error('offline'); };
  await assert.rejects(signOut(), /offline/);
  assert.equal(getSession(), null);
});

test('CSV download uses an authorization header and a local blob URL', async () => {
  await signIn('private-key');
  let clicked;
  globalThis.document = { createElement: () => {
    const link = { click() { clicked = { href: link.href, download: link.download }; } };
    return link;
  } };
  await downloadCsv('/api/profiles/export', 'profiles.csv');
  assert.equal(calls.at(-1).init.headers.get('Authorization'), 'Bearer synthetic-session');
  assert.equal(calls.at(-1).url.includes('token='), false);
  assert.equal(clicked.download, 'profiles.csv');
  assert.ok(clicked.href.startsWith('blob:'));
});
