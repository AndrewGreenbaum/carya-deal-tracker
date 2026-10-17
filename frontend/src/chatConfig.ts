/** Resolve an optional same-origin proxy path without exposing gateway credentials. */
export function getChatUrl(configuredPath: string | undefined, origin: string): string | null {
  if (!configuredPath || !/^\/[A-Za-z0-9/_-]+$/.test(configuredPath) || configuredPath.startsWith('//')) {
    return null;
  }
  const url = new URL(origin);
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = configuredPath;
  url.search = '';
  url.hash = '';
  url.username = '';
  url.password = '';
  return url.toString();
}
