import { describe, expect, it } from "vitest";
import { donutSlices } from "./raciLoad";

describe("donutSlices", () => {
  it("splits a circle proportionally across counts", () => {
    const slices = donutSlices(
      [
        { key: "msp", count: 3 },
        { key: "customer", count: 1 },
      ],
      100
    );
    expect(slices[0].pct).toBe(75);
    expect(slices[1].pct).toBe(25);
    expect(slices[0].dashArray).toBe("75 25");
    expect(slices[1].dashArray).toBe("25 75");
  });

  it("offsets each slice by the cumulative length of the ones before it", () => {
    const slices = donutSlices(
      [
        { key: "a", count: 1 },
        { key: "b", count: 1 },
        { key: "c", count: 2 },
      ],
      100
    );
    expect(slices[0].dashOffset).toBe(-0);
    expect(slices[1].dashOffset).toBe(-25);
    expect(slices[2].dashOffset).toBe(-50);
  });

  it("all-zero counts degrade to 0% per slice, not a divide-by-zero NaN", () => {
    const slices = donutSlices(
      [
        { key: "msp", count: 0 },
        { key: "customer", count: 0 },
      ],
      100
    );
    for (const s of slices) {
      expect(s.pct).toBe(0);
      expect(Number.isNaN(s.pct)).toBe(false);
    }
  });

  it("a single count with the whole total fills the entire circle", () => {
    const slices = donutSlices([{ key: "msp", count: 5 }], 62.8);
    expect(slices[0].pct).toBe(100);
    expect(slices[0].dashArray).toBe("62.8 0");
  });

  it("preserves input order and count values on the output", () => {
    const slices = donutSlices(
      [
        { key: "msp", count: 7 },
        { key: "customer", count: 2 },
        { key: "other", count: 1 },
      ],
      100
    );
    expect(slices.map((s) => s.key)).toEqual(["msp", "customer", "other"]);
    expect(slices.map((s) => s.count)).toEqual([7, 2, 1]);
  });
});
