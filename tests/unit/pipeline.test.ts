import { expect, test } from 'bun:test';

import { ping } from '../../src/loop/index.ts';

test('ping answers pong through the package entry', () => {
  expect(ping()).toBe('pong');
});
