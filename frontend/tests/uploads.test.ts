import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  uploadUrl,
  validateUpload,
  maxVideoBytes,
} from '../src/shared/lib/uploads.ts';
import { type Identifier } from '../src/shared/api/identifiers.ts';

const origin = 'https://example.com';
const plan = {
  id: '9007199254740993' as Identifier,
  status: 'uploading',
  size: 17,
  part_size: 16,
  parallel_parts: 3,
  expires_at: '2026-11-16T12:00:00Z',
  parts: [
    {
      number: 1,
      size: 16,
      url: '/uploads/bucket/key?partNumber=1&signature=test',
    },
    {
      number: 2,
      size: 1,
      url: '/uploads/bucket/key?partNumber=2&signature=test',
    },
  ],
};
test('signed upload URLs stay within same-origin storage PUT gateway', () => {
  assert.equal(
    uploadUrl(plan.parts[0]!.url, origin),
    origin + plan.parts[0]!.url,
  );
  for (const value of [
    '//other.example/uploads/a?sig=b',
    'https://user:pass@example.com/uploads/a?sig=b',
    '/uploads/../api/v1/videos',
    '/uploads/%2e%2e/api',
    'javascript:alert(1)',
    '/api/v1/videos',
    '/uploads/a#fragment',
  ])
    assert.throws(() => uploadUrl(value, origin));
});
test('multipart plans preserve exact IDs, part boundaries and omitted acknowledged parts', () => {
  assert.equal(validateUpload(plan, 17, origin).id, '9007199254740993');
  assert.equal(
    validateUpload({ ...plan, parts: [plan.parts[1]!] }, 17, origin).parts
      .length,
    1,
  );
  assert.equal(
    validateUpload(
      { ...plan, size: maxVideoBytes, parts: [] },
      maxVideoBytes,
      origin,
    ).size,
    maxVideoBytes,
  );
  for (const value of [
    { ...plan, size: 18 },
    { ...plan, part_size: 0 },
    { ...plan, parallel_parts: 0 },
    { ...plan, parts: [plan.parts[0]!, plan.parts[0]!] },
    { ...plan, parts: [{ ...plan.parts[1]!, size: 2 }] },
    { ...plan, parts: [{ ...plan.parts[0]!, number: 3 }] },
    { ...plan, status: 'uploaded' },
  ])
    assert.throws(() => validateUpload(value, 17, origin));
  for (const size of [0, -1, maxVideoBytes + 1, 1.5, Infinity])
    assert.throws(() =>
      validateUpload({ ...plan, size, parts: [] }, size, origin),
    );
});
