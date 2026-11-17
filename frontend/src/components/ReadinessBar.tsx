import { readiness, STATE_WORD } from "../lib/readiness";
import type { ControlStateRow } from "../types";

// Colour is never the only cue: each segment carries its short name and a
// fill mark (full, half, empty), and says its state in words to a screen
// reader and on hover. Ratios are in styles.css beside `.rb-seg`.
export function ReadinessBar({ row }: { row: ControlStateRow }) {
  const segments = readiness(row);
  return (
    <span className="rb" role="group" aria-label="Readiness">
      {segments.map((s) => {
        const said = `${s.name}: ${STATE_WORD[s.state]} (${s.detail})`;
        return (
          <span
            key={s.key}
            className={`rb-seg rb-${s.state}`}
            data-segment={s.key}
            data-state={s.state}
            title={said}
            aria-label={said}
            role="img"
          >
            <span className="rb-mark" aria-hidden="true" />
            <span aria-hidden="true">{s.short}</span>
          </span>
        );
      })}
    </span>
  );
}
