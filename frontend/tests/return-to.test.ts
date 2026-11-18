import assert from 'node:assert/strict';
import { test } from 'node:test';
import { safeReturnTo } from '../src/entities/session/model/return-to.ts';

const origin = 'https://youtube.example';
test('return destination preserves internal query/hash without evaluating nested URLs', () => {
  for (const value of [
    '/',
    '/watch?v=9007199254740993',
    '/studio/upload?video=9007199254740994',
    '/@channel/videos',
    '/results?search_query=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1#searching',
  ])
    assert.equal(safeReturnTo(value, origin), value);
  assert.equal(safeReturnTo('/one/../watch?v=1', origin), '/watch?v=1');
});
test('return destination rejects external, ambiguous and authentication-loop paths', () => {
  for (const value of [
    undefined,
    null,
    42,
    ['/watch'],
    ['/watch', '/studio'],
    'https://example.com/',
    '//example.com/',
    '///example.com/',
    'javascript:alert(1)',
    'data:text/html,test',
    'watch?v=1',
    '/\\example.com/',
    '/%5cexample.com/',
    '/%2f%2fexample.com/',
    '/watch\n?x=1',
    '/%0d%0aevil',
    '/%7fevil',
    '/%',
    '/%C0%AF',
    '/login',
    '/register?returnTo=/watch',
    '/LOGIN/',
    '/Register///',
    '/%6cogin',
    '/%72egister/',
    '/one/../login',
    '/%2e%2e/register',
  ])
    assert.equal(safeReturnTo(value, origin), '/', String(value));
});
