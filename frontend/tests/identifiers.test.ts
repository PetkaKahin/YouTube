import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  isIdentifier,
  parseApiJson,
  serializeApiJson,
} from '../src/shared/api/identifiers.ts';

test('int64 identifiers retain exact digits across nested objects and lists', () => {
  for (const id of [
    '1',
    '9007199254740991',
    '9007199254740992',
    '9007199254740993',
    '9223372036854775807',
  ]) {
    const value = parseApiJson(
      `{"id":${id},"channel":{"id":${id}},"video_ids":[${id}],"duration_seconds":1.25,"title":"id: ${id}"}`,
    ) as {
      id: string;
      channel: { id: string };
      video_ids: string[];
      duration_seconds: number;
      title: string;
    };
    assert.equal(value.id, id);
    assert.equal(value.channel.id, id);
    assert.deepEqual(value.video_ids, [id]);
    assert.equal(value.duration_seconds, 1.25);
    assert.equal(value.title, `id: ${id}`);
    assert.ok(isIdentifier(value.id));
    assert.equal(
      serializeApiJson(value),
      `{"id":${id},"channel":{"id":${id}},"video_ids":[${id}],"duration_seconds":1.25,"title":"id: ${id}"}`,
    );
  }
});

test('invalid integer identifiers fail instead of rounding or aliasing another object', () => {
  for (const token of ['0', '-1', '1.5', '1e3', '9223372036854775808']) {
    assert.throws(() => parseApiJson(`{"id":${token}}`));
  }
  for (const value of [
    '0',
    '-1',
    '01',
    '1e3',
    '9223372036854775808',
    9007199254740992,
    null,
  ])
    assert.equal(isIdentifier(value), false);
  assert.throws(() => serializeApiJson({ id: '9223372036854775808' }));
  assert.throws(() => serializeApiJson({ id: 9007199254740992 }));
  assert.throws(() => parseApiJson('{"count":9007199254740993}'));
});

test('user text and UUIDs are data, including JSON-like strings and prototype keys', () => {
  const body = {
    title: '{"id":9223372036854775807}<script>alert(1)</script>',
    password: ' 123456 ',
    request_id: 'a4b76193-201b-48e0-afb9-de69458bcacb',
    id: null,
  };
  assert.deepEqual(parseApiJson(serializeApiJson(body)!), body);
  const parsed = parseApiJson(
    '{"__proto__":{"polluted":true},"id":1}',
  ) as Record<string, unknown>;
  assert.equal(Object.getPrototypeOf(parsed), Object.prototype);
  assert.equal(({} as Record<string, unknown>).polluted, undefined);
  assert.ok(Object.hasOwn(parsed, '__proto__'));
  assert.throws(() => parseApiJson('{"id":1,"id":2}'));
  assert.throws(() => parseApiJson('not JSON'));
  assert.equal(serializeApiJson(undefined), undefined);
});
