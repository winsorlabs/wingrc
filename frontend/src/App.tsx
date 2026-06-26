import { useEffect, useState } from "react";

type View = { id: string; title: string; control_ids: string[]; entity_type: string };

// Minimal pilot shell: confirm the API is alive and list the CMMC views the
// scope graph can project. The real UI (scope browser, import-reconcile,
// bundle generation) builds out from here.
export function App() {
  const [health, setHealth] = useState<string>("checking…");
  const [views, setViews] = useState<View[]>([]);

  useEffect(() => {
    fetch("/api/health").then((r) => r.json()).then((d) => setHealth(d.status)).catch(() => setHealth("unreachable"));
    fetch("/api/catalog/views").then((r) => r.json()).then(setViews).catch(() => setViews([]));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 720, margin: "3rem auto", padding: "0 1rem" }}>
      <h1>WinGRC</h1>
      <p>Open CMMC scope and documentation tooling. API: <strong>{health}</strong></p>
      <h2>Available list views</h2>
      <ul>
        {views.map((v) => (
          <li key={v.id}>
            <strong>{v.title}</strong> — {v.control_ids.join(", ")} ({v.entity_type})
          </li>
        ))}
        {views.length === 0 && <li>No views loaded (is the backend running?).</li>}
      </ul>
    </main>
  );
}
