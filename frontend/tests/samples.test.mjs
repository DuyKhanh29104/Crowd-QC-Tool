import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await fs.readFile(new URL('../src/qc/samples.ts', import.meta.url), 'utf8');
const {outputText} = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext}});
const {nearestSample, samplesBetween} = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));

test('nearest sample handles empty input, boundaries, exact hits and ties', () => {
  const rows = [{time: 1}, {time: 3}, {time: 8}];
  assert.equal(nearestSample([], 0), undefined);
  for (const [time, expected] of [[-2, 0], [1, 0], [2, 0], [3, 1], [5.5, 1], [6, 2], [20, 2]]) {
    assert.equal(nearestSample(rows, time), rows[expected]);
  }
});

test('lookup and trajectory windows preserve results across irregular timestamps', () => {
  const rows = Array.from({length: 5000}, (_, i) => ({time: i * .037 + Math.floor(i / 7) * .013}));
  for (let seconds = -1; seconds < 200; seconds += .217) {
    const expected = rows.reduce((best, row) => Math.abs(row.time - seconds) < Math.abs(best.time - seconds) ? row : best);
    assert.equal(nearestSample(rows, seconds), expected);
    assert.deepEqual(samplesBetween(rows, seconds - 3, seconds), rows.filter(row => row.time >= seconds - 3 && row.time <= seconds));
  }
  assert.deepEqual(samplesBetween(rows, rows[4].time, rows[9].time), rows.slice(4, 10));
  assert.deepEqual(samplesBetween([], 0, 1), []);
});

test('nearest lookup reads logarithmically many timestamps on a long video', () => {
  let reads = 0;
  const rows = Array.from({length: 100000}, (_, i) => ({get time() { reads++; return i / 5; }}));
  assert.equal(nearestSample(rows, 10000.05), rows[50000]);
  assert.ok(reads < 25, `Read ${reads} timestamps`);
});
