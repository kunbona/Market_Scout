import test from 'node:test';
import assert from 'node:assert/strict';

import { formatFlowYi } from '../src/lib/flowFormatters.ts';

test('formatFlowYi converts raw yuan to yi (divides by 1e8)', () => {
  // 97.6亿 = 9,760,000,000 元
  assert.equal(formatFlowYi(9_760_000_000), '97.60亿');
  // -12.34亿 = -1,234,000,000 元
  assert.equal(formatFlowYi(-1_234_000_000), '-12.34亿');
});

test('formatFlowYi returns placeholder for missing values', () => {
  assert.equal(formatFlowYi(null), '--');
  assert.equal(formatFlowYi(undefined), '--');
});
