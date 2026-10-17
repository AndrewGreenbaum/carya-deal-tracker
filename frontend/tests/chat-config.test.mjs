import assert from 'node:assert/strict';
import test from 'node:test';
import { getChatUrl } from '../src/chatConfig.ts';

test('chat is disabled unless a safe same-origin path is explicitly configured', () => {
  for (const path of [undefined, '', 'wss://remote.invalid/chat', '//remote.invalid/chat', '/chat?token=secret', '/chat#secret', '/chat%3Ftoken', '/chat\\remote', '/../chat']) {
    assert.equal(getChatUrl(path, 'https://dashboard.invalid'), null);
  }
});

test('local proxy uses the page host and appropriate WebSocket protocol without credentials', () => {
  assert.equal(getChatUrl('/api/chat', 'https://dashboard.invalid'), 'wss://dashboard.invalid/api/chat');
  assert.equal(getChatUrl('/api/chat', 'http://localhost:3000'), 'ws://localhost:3000/api/chat');
  assert.equal(getChatUrl('/api/chat', 'https://user:password@dashboard.invalid?token=secret#ignored'), 'wss://dashboard.invalid/api/chat');
  assert.equal(getChatUrl('/api/chat', 'file:///tmp'), null);
});
