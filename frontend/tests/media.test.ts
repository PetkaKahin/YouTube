import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  mediaUrl,
  playbackTime,
  qualityName,
} from '../src/shared/lib/media.ts';

test('media URLs cannot leave the allowed origin or publication', () => {
  const origin = 'https://example.com';
  const prefix = '/api/v1/videos/9007199254740993/hls/';
  assert.equal(
    mediaUrl(`${prefix}tracks/v1/init.mp4`, prefix, origin),
    `${origin}${prefix}tracks/v1/init.mp4`,
  );
  for (const value of [
    'javascript:alert(1)',
    `https://other.example${prefix}master.m3u8`,
    `//other.example${prefix}master.m3u8`,
    `${prefix}../preview.webp`,
    `${prefix}%2e%2e/preview.webp`,
    '/api/v1/videos/9007199254740992/hls/master.m3u8',
    `${prefix}master.m3u8?url=evil`,
    `${prefix}master.m3u8#evil`,
    `https://user:password@example.com${prefix}master.m3u8`,
  ])
    assert.throws(() => mediaUrl(value, prefix, origin));
});

test('quality names preserve portrait, wide and below-360p server rungs', () => {
  const ready = [
    { quality: 360, range: 'sdr' },
    { quality: 480, range: 'sdr' },
    { quality: 720, range: 'sdr' },
    { quality: 1080, range: 'pq' },
  ];
  assert.equal(qualityName(640, 360, 'SDR', ready), '360p');
  assert.equal(qualityName(480, 854, 'SDR', ready), '480p');
  assert.equal(qualityName(1280, 320, 'SDR', ready), '720p');
  assert.equal(qualityName(1920, 1080, 'PQ', ready), '1080p HDR');
  assert.equal(
    qualityName(96, 160, 'SDR', [{ quality: 96, range: 'sdr' }]),
    '96p',
  );
  assert.equal(qualityName(3840, 2160, 'HLG', ready), undefined);
});

test('playback time handles end boundaries and unloaded media', () => {
  assert.equal(playbackTime(Number.NaN), '0:00');
  assert.equal(playbackTime(Infinity), '0:00');
  assert.equal(playbackTime(-1), '0:00');
  assert.equal(playbackTime(59.9), '0:59');
  assert.equal(playbackTime(60), '1:00');
  assert.equal(playbackTime(3600), '1:00:00');
});
