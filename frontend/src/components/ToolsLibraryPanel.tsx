import { useEffect, useState } from "react";
import { api } from "../api";
import type { ProductLibraryItem } from "../types";
import { ToolDetailPanel } from "./ToolDetailPanel";
import { ToolImportWizard } from "./ToolImportWizard";

// Deployment-tier baseline library management (G.9) — see this file's own
// docstring in backend/app/routers/admin_products.py for the full scope.
// This screen manages the library only: it never writes OrgProduct, and
// there is no field-by-field mapping editor here. The YAML is the
// authoring format; this ingests and reviews it.
export function ToolsLibraryPanel() {
  const [products, setProducts] = useState<ProductLibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);

  function load() {
    setLoading(true);
    setError(null);
    api
      .listToolsLibrary()
      .then(setProducts)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  if (selected) {
    return (
      <ToolDetailPanel
        productId={selected}
        onBack={() => {
          setSelected(null);
          load();
        }}
        onChanged={load}
      />
    );
  }

  return (
    <div className="tools-library-panel">
      <div className="products-panel-header" style={{ padding: 0, border: "none", marginBottom: "0.75rem" }}>
        <span className="products-panel-title">Baseline Library</span>
        <button className="btn-primary btn-sm" onClick={() => setShowImport(true)}>Import baseline</button>
      </div>

      {loading ? (
        <div className="loading">Loading library…</div>
      ) : error ? (
        <div className="form-error">{error}</div>
      ) : products.length === 0 ? (
        <div className="empty">No products in the library yet.</div>
      ) : (
        <div className="table-scroll">
          <table className="contacts-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Provider</th>
                <th>Category</th>
                <th>Framework</th>
                <th>Status</th>
                <th>Controls</th>
                <th>Objectives</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.id} onClick={() => setSelected(p.id)} style={{ cursor: "pointer" }}>
                  <td><strong>{p.name}</strong></td>
                  <td>{p.provider}</td>
                  <td>{p.category} · {p.asset_type}</td>
                  <td>{p.framework_name}</td>
                  <td>
                    <span className={`status-badge ${p.is_published ? "status-active" : "status-inactive"}`}>
                      {p.is_published ? "Published" : "Unpublished"}
                    </span>
                  </td>
                  <td>{p.control_count}</td>
                  <td>{p.objective_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showImport && (
        <ToolImportWizard
          onClose={() => setShowImport(false)}
          onApplied={() => {
            setShowImport(false);
            load();
          }}
        />
      )}
    </div>
  );
}
