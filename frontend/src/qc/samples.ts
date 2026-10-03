/** Analysis samples are stored in strictly increasing source timestamp order. */
export function sampleIndexAtOrAfter<T extends {time: number}>(rows: readonly T[], seconds: number): number {
  let low = 0;
  let high = rows.length;
  while (low < high) {
    const middle = low + Math.floor((high - low) / 2);
    if (rows[middle].time < seconds) low = middle + 1;
    else high = middle;
  }
  return low;
}

export function nearestSample<T extends {time: number}>(rows: readonly T[], seconds: number): T | undefined {
  const index = sampleIndexAtOrAfter(rows, seconds);
  if (index === 0) return rows[0];
  if (index === rows.length) return rows[index - 1];
  // Preserve the earlier sample when both timestamps are equally close.
  return seconds - rows[index - 1].time <= rows[index].time - seconds ? rows[index - 1] : rows[index];
}

export function samplesBetween<T extends {time: number}>(rows: readonly T[], start: number, end: number): readonly T[] {
  const first = sampleIndexAtOrAfter(rows, start);
  let last = sampleIndexAtOrAfter(rows, end);
  if (last < rows.length && rows[last].time === end) last++;
  return rows.slice(first, last);
}
