// Pure data transform for the org dashboard's assignment-load widgets
// (G.7 Part 4) -- matches this codebase's existing convention of testing
// pure functions in lib/, not components (see radarChart.ts). Hand-rolled
// SVG donut geometry, no charting library -- same precedent radarChart.ts
// set (docs/roadmap.md item C.2).

export interface DonutSlice {
  key: string;
  count: number;
  pct: number; // 0-100, of the total across all slices
  // SVG stroke-dasharray/stroke-dashoffset values for drawing this slice as
  // an arc of a circle with the given circumference -- stack multiple
  // <circle> elements sharing center/radius, each with its own dashArray/
  // dashOffset, to build a donut. dashOffset is negative (SVG dash offset
  // convention: negative moves the dash pattern forward along the path).
  dashArray: string;
  dashOffset: number;
}

// counts with a total of 0 (no R assignments at all yet) still returns one
// slice per input with pct=0 -- the caller decides how to render "no data"
// (e.g. a single neutral-colored ring), not this function.
export function donutSlices(
  counts: { key: string; count: number }[],
  circumference: number
): DonutSlice[] {
  const total = counts.reduce((sum, c) => sum + c.count, 0);
  let offset = 0;
  return counts.map((c) => {
    const pct = total > 0 ? (c.count / total) * 100 : 0;
    const len = (pct / 100) * circumference;
    const slice: DonutSlice = {
      key: c.key,
      count: c.count,
      pct,
      dashArray: `${len} ${circumference - len}`,
      dashOffset: -offset,
    };
    offset += len;
    return slice;
  });
}
