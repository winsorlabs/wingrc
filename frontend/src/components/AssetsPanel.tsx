import { useEffect, useState } from "react";
import { api } from "../api";
import type { ScopeEntity } from "../types";
import { AssetDrawer } from "./AssetDrawer";
import { AssetImportWizard } from "./AssetImportWizard";

const TYPE_LABELS: Record<string, string> = { device: "Device", software: "Software" };

interface Props {
  orgId: string;
  canWrite: boolean;
}

export function AssetsPanel({ orgId, canWrite }: Props) {
  const [assets, setAssets] = useState<ScopeEntity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drawerAsset, setDrawerAsset] = useState<ScopeEntity | null | undefined>(undefined);
  const [showImport, setShowImport] = useState(false);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId]);

  function load() {
    setLoading(true);
    setError(null);
    Promise.all([api.getScope(orgId, "device"), api.getScope(orgId, "software")])
      .then(([devices, software]) => {
        setAssets(
          [...devices, ...software].sort((a, b) => a.natural_key.localeCompare(b.natural_key))
        );
        setLoading(false);
      })
      .catch(() => {
        setError("Could not load assets");
        setLoading(false);
      });
  }

  function handleSaved(a: ScopeEntity) {
    setAssets((prev) => {
      const idx = prev.findIndex((p) => p.id === a.id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = a;
        return next;
      }
      return [...prev, a].sort((x, y) => x.natural_key.localeCompare(y.natural_key));
    });
    setDrawerAsset(undefined);
  }

  function handleDeleted(id: string) {
    setAssets((prev) => prev.filter((a) => a.id !== id));
    setDrawerAsset(undefined);
  }

  if (loading) return <div className="loading">Loading assets…</div>;
  if (error) return <div className="form-error">{error}</div>;

  return (
    <div className="contacts-panel">
      <div className="contacts-panel-header">
        {canWrite && (
          <>
            <button className="btn-ghost btn-sm" onClick={() => setShowImport(true)}>
              Import from Workbook
            </button>
            <button className="btn-primary btn-sm" onClick={() => setDrawerAsset(null)}>
              + Add Asset
            </button>
          </>
        )}
      </div>

      {assets.length === 0 ? (
        <div className="contacts-empty">
          No hardware or software assets recorded yet. Add one manually or import an
          Authorized-Entities workbook.
        </div>
      ) : (
        <div className="table-scroll">
          <table className="contacts-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Name</th>
                <th>Category</th>
                <th>Make / OEM</th>
                <th>Model</th>
                <th>Version</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {assets.map((a) => (
                <tr key={a.id}>
                  <td><span className="affiliation-badge">{TYPE_LABELS[a.entity_type] ?? a.entity_type}</span></td>
                  <td>{a.natural_key}</td>
                  <td>{a.scope_category ?? "—"}</td>
                  <td>{(a.attributes.make_oem as string | null) ?? "—"}</td>
                  <td>{(a.attributes.model as string | null) ?? "—"}</td>
                  <td>{(a.attributes.version as string | null) ?? "—"}</td>
                  <td>{a.status}</td>
                  <td>
                    {canWrite && (
                      <button className="btn-ghost btn-sm" onClick={() => setDrawerAsset(a)}>Edit</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {drawerAsset !== undefined && (
        <AssetDrawer
          orgId={orgId}
          asset={drawerAsset}
          canWrite={canWrite}
          onClose={() => setDrawerAsset(undefined)}
          onSaved={handleSaved}
          onDeleted={handleDeleted}
        />
      )}

      {showImport && (
        <AssetImportWizard
          orgId={orgId}
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
