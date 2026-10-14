import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  clampSchedule,
  isSuccessfulStatus,
  localDateKey,
  nextDailyRun,
} from '../core.mjs';

test('manifest is valid MV3 and scoped to the target site', async () => {
  const text = await readFile(new URL('../manifest.json', import.meta.url), 'utf8');
  const manifest = JSON.parse(text);
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.host_permissions, ['https://llmapi.isrc.ac.cn/*']);
  assert.equal(manifest.background.type, 'module');
});

test('schedule values are clamped', () => {
  assert.deepEqual(clampSchedule({ enabled: false, hour: 99, minute: -3 }), {
    enabled: false,
    hour: 23,
    minute: 0,
  });
});

test('next run stays today when target time is ahead', () => {
  const now = new Date(2026, 9, 13, 7, 30, 0);
  const next = nextDailyRun(8, 10, now);
  assert.equal(next.getFullYear(), 2026);
  assert.equal(next.getMonth(), 9);
  assert.equal(next.getDate(), 13);
  assert.equal(next.getHours(), 8);
  assert.equal(next.getMinutes(), 10);
});

test('next run advances one day when target time has passed', () => {
  const now = new Date(2026, 9, 13, 9, 0, 0);
  const next = nextDailyRun(8, 10, now);
  assert.equal(next.getDate(), 14);
});

test('local date key and successful statuses are stable', () => {
  assert.equal(localDateKey(new Date(2026, 0, 2, 12, 0, 0)), '2026-01-02');
  assert.equal(isSuccessfulStatus('success'), true);
  assert.equal(isSuccessfulStatus('already'), true);
  assert.equal(isSuccessfulStatus('error'), false);
});

test('extension entry files exist', async () => {
  await Promise.all(
    ['service-worker.js', 'popup.html', 'popup.js', 'popup.css'].map((file) =>
      readFile(new URL(`../${file}`, import.meta.url), 'utf8'),
    ),
  );
});
