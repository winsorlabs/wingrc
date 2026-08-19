import { FAMILY_ORDER } from "./families";
import type { FamilyHeatmapEntry } from "../types";

// Pure data transform for the dashboard's family radar chart (extracted so
// it's unit-testable without rendering SVG — matches this codebase's
// existing convention of testing pure functions in lib/, not components;
// see filters.test.ts / permissions.test.ts).
export interface RadarPoint {
  family: string;
  pct: number; // 0-100
  angle: number; // radians; 0 = up (12 o'clock), increases clockwise
  // Position on a unit circle scaled by pct/100 — 0 at center, 1 at the
  // outer edge in this axis's direction. The rendering component multiplies
  // these by whatever pixel radius it wants; kept unit-scaled here so the
  // geometry is exact and testable independent of any chosen chart size.
  unitX: number;
  unitY: number;
}

// Reuses the same 14-family fixed axis set every time, in FAMILY_ORDER —
// a family absent from `entries` (no control_state rows for it in this
// assessment) still gets a spoke, at 0%, rather than being omitted. This
// is the "doesn't break on an org with zero data in some families" case
// from G.3's own exit-criteria pattern applied to this widget.
export function familyRadarPoints(entries: FamilyHeatmapEntry[]): RadarPoint[] {
  const byFamily = new Map(entries.map((e) => [e.family, e]));
  const n = FAMILY_ORDER.length;

  return FAMILY_ORDER.map((family, i) => {
    const entry = byFamily.get(family);
    const pct = entry && entry.controls_total > 0 ? (entry.controls_met / entry.controls_total) * 100 : 0;
    const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
    const r = pct / 100;
    return {
      family,
      pct,
      angle,
      unitX: r * Math.cos(angle),
      unitY: r * Math.sin(angle),
    };
  });
}
