import { familyRadarPoints } from "../lib/radarChart";
import type { FamilyHeatmapEntry, SprsWidgetData } from "../types";

// Cards both dashboard views draw (Q.0): the SPRS score and the family
// radar. The radar moved to the environment view; the family bar list
// stays on the assessment view (OrgDashboard.tsx).

// 100 SVG user units = 100% completion. Padding leaves room for the axis
// labels (family abbreviations) outside the outer ring. Kept as plain
// constants, not props — this chart always plots the same fixed 14 axes,
// no configurability needed (per the request: "no need for complex
// interactivity").
const RADAR_SIZE = 100;
const RADAR_PADDING = 26;
const RADAR_CENTER = RADAR_SIZE + RADAR_PADDING;
const RADAR_RINGS = [25, 50, 75, 100];

export function FamilyRadarCard({
  entries,
  emptyText = "No controls in this assessment yet.",
}: {
  entries: FamilyHeatmapEntry[];
  emptyText?: string;
}) {
  const points = familyRadarPoints(entries);
  const toScreen = (unitX: number, unitY: number) =>
    `${RADAR_CENTER + unitX * RADAR_SIZE},${RADAR_CENTER + unitY * RADAR_SIZE}`;
  const ringPoints = (pct: number) =>
    points
      .map((p) => toScreen((pct / 100) * Math.cos(p.angle), (pct / 100) * Math.sin(p.angle)))
      .join(" ");
  const dataPolygon = points.map((p) => toScreen(p.unitX, p.unitY)).join(" ");

  return (
    <div className="card">
      <h2>Family Completion (Radar)</h2>
      {entries.length === 0 && <div className="empty">{emptyText}</div>}
      <svg
        viewBox={`0 0 ${RADAR_CENTER * 2} ${RADAR_CENTER * 2}`}
        className="radar-chart"
        role="img"
        aria-label="Per-family completion percentage, plotted as a radar chart across all 14 control families"
      >
        {RADAR_RINGS.map((pct) => (
          <polygon key={pct} points={ringPoints(pct)} className="radar-grid-ring" />
        ))}
        {points.map((p) => (
          <line
            key={p.family}
            x1={RADAR_CENTER}
            y1={RADAR_CENTER}
            x2={RADAR_CENTER + Math.cos(p.angle) * RADAR_SIZE}
            y2={RADAR_CENTER + Math.sin(p.angle) * RADAR_SIZE}
            className="radar-axis-line"
          />
        ))}
        <polygon points={dataPolygon} className="radar-data-polygon" />
        {points.map((p) => (
          <circle
            key={p.family}
            cx={RADAR_CENTER + p.unitX * RADAR_SIZE}
            cy={RADAR_CENTER + p.unitY * RADAR_SIZE}
            r={3}
            className="radar-data-point"
          >
            <title>{p.family}: {Math.round(p.pct)}%</title>
          </circle>
        ))}
        {points.map((p) => {
          const labelR = RADAR_SIZE + 14;
          return (
            <text
              key={p.family}
              x={RADAR_CENTER + Math.cos(p.angle) * labelR}
              y={RADAR_CENTER + Math.sin(p.angle) * labelR}
              className="radar-label"
              textAnchor="middle"
              dominantBaseline="middle"
            >
              {p.family}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

export function SprsCard({ sprs }: { sprs: SprsWidgetData }) {
  const first = sprs.trajectory[0]?.score;
  const last = sprs.trajectory[sprs.trajectory.length - 1]?.score;
  const trend = first !== undefined && last !== undefined ? last - first : null;
  return (
    <div className="card">
      <h2>SPRS Score</h2>
      <div className="dashboard-big-stat">{sprs.current_score ?? "—"}</div>
      {trend !== null && sprs.trajectory.length > 1 && (
        <div className="item-meta">
          {trend > 0 ? "▲" : trend < 0 ? "▼" : "—"} {trend > 0 ? "+" : ""}{trend} over {sprs.trajectory.length} recomputes
        </div>
      )}
      {sprs.trajectory.length === 0 && <div className="empty">No score history yet.</div>}
    </div>
  );
}
