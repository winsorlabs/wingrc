import { describe, expect, it } from "vitest";
import type { FamilyHeatmapEntry } from "../types";
import { FAMILY_ORDER } from "./families";
import { familyRadarPoints } from "./radarChart";

function entry(overrides: Partial<FamilyHeatmapEntry> = {}): FamilyHeatmapEntry {
  return { family: "AC", controls_met: 0, controls_total: 1, ...overrides };
}

describe("familyRadarPoints", () => {
  it("always returns exactly 14 points, one per FAMILY_ORDER family, in order", () => {
    const points = familyRadarPoints([entry({ family: "AC", controls_met: 1, controls_total: 1 })]);
    expect(points).toHaveLength(14);
    expect(points.map((p) => p.family)).toEqual([...FAMILY_ORDER]);
  });

  it("a family missing from entries gets a spoke at 0%, not omitted or crashed", () => {
    // Only AC has data; the other 13 families have zero rows in this
    // assessment (e.g. a brand-new assessment with only one control seeded).
    const points = familyRadarPoints([entry({ family: "AC", controls_met: 1, controls_total: 2 })]);
    const si = points.find((p) => p.family === "SI");
    expect(si).toBeDefined();
    expect(si!.pct).toBe(0);
    expect(si!.unitX).toBeCloseTo(0);
    expect(si!.unitY).toBeCloseTo(0);
  });

  it("maps met/total to a 0-100 percentage per family", () => {
    const points = familyRadarPoints([
      entry({ family: "AC", controls_met: 1, controls_total: 4 }), // 25%
      entry({ family: "AU", controls_met: 3, controls_total: 4 }), // 75%
      entry({ family: "SI", controls_met: 2, controls_total: 2 }), // 100%
    ]);
    expect(points.find((p) => p.family === "AC")!.pct).toBe(25);
    expect(points.find((p) => p.family === "AU")!.pct).toBe(75);
    expect(points.find((p) => p.family === "SI")!.pct).toBe(100);
  });

  it("controls_total of 0 degrades to 0%, not a divide-by-zero NaN", () => {
    const points = familyRadarPoints([entry({ family: "AC", controls_met: 0, controls_total: 0 })]);
    const ac = points.find((p) => p.family === "AC")!;
    expect(ac.pct).toBe(0);
    expect(Number.isNaN(ac.unitX)).toBe(false);
    expect(Number.isNaN(ac.unitY)).toBe(false);
  });

  it("first family (AC) sits at the top of the circle (12 o'clock)", () => {
    const points = familyRadarPoints([entry({ family: "AC", controls_met: 1, controls_total: 1 })]);
    const ac = points[0];
    expect(ac.family).toBe("AC");
    expect(ac.angle).toBeCloseTo(-Math.PI / 2);
    // 100% at the top: x ~ 0, y ~ -1 (SVG y-axis points down).
    expect(ac.unitX).toBeCloseTo(0);
    expect(ac.unitY).toBeCloseTo(-1);
  });

  it("spokes are evenly spaced by angle across all 14 families", () => {
    const points = familyRadarPoints([]);
    const step = (Math.PI * 2) / 14;
    for (let i = 1; i < points.length; i++) {
      expect(points[i].angle - points[i - 1].angle).toBeCloseTo(step);
    }
  });

  it("a family at 0% sits exactly at the center regardless of its angle", () => {
    const points = familyRadarPoints([]);
    for (const p of points) {
      expect(p.unitX).toBeCloseTo(0);
      expect(p.unitY).toBeCloseTo(0);
    }
  });

  it("empty entries list produces all 14 spokes at 0%, not a crash", () => {
    const points = familyRadarPoints([]);
    expect(points).toHaveLength(14);
    expect(points.every((p) => p.pct === 0)).toBe(true);
  });
});
