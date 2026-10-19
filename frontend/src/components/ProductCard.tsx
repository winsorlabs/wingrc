import { useState } from "react";
import { api } from "../api";
import type { ProductRow } from "../types";

interface Props {
  product: ProductRow;
  // The library's other products, any of which the person may name as
  // this one's replacement when deactivating it.
  others: ProductRow[];
  orgId: string;
  assessmentId: string;
  canWrite: boolean;
  onActivated: () => void;
  onDeactivated: () => void;
}

export function ProductCard({ product, others, orgId, assessmentId, canWrite, onActivated, onDeactivated }: Props) {
  const [activating, setActivating] = useState(false);
  const [deactivating, setDeactivating] = useState(false);
  const [asking, setAsking] = useState(false);
  const [replacedBy, setReplacedBy] = useState("");
  const [movingVersion, setMovingVersion] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleActivate() {
    setActivating(true);
    setError(null);
    try {
      await api.activateProduct(orgId, assessmentId, product.id);
      onActivated();
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setActivating(false);
    }
  }

  async function handleDeactivate() {
    setDeactivating(true);
    setError(null);
    try {
      await api.deactivateProduct(orgId, assessmentId, product.id, replacedBy || null);
      setAsking(false);
      onDeactivated();
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setDeactivating(false);
    }
  }

  // Baseline versioning (roadmap item P): offered only when this org's
  // pin is behind the product's current version. Resolves the target
  // version's id from the versions list (ProductRow only carries the
  // number) rather than assuming current_version_number's id -- the
  // library's own detail screen is the source of truth for that mapping.
  async function handleMoveVersion() {
    setMovingVersion(true);
    setError(null);
    try {
      const versions = await api.listProductVersions(product.id);
      const target = versions.find((v) => v.is_current);
      if (!target) {
        throw new Error("No current version found for this product.");
      }
      await api.moveProductVersion(orgId, assessmentId, product.id, target.id);
      onActivated();
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setMovingVersion(false);
    }
  }

  return (
    <div className={`product-card${product.is_active ? " product-card-active" : ""}`}>
      <div className="product-card-header">
        <div className="product-card-name">{product.name}</div>
        <div className="product-card-meta">
          <span className="product-badge product-badge-provider">{product.provider}</span>
          <span className="product-badge product-badge-category">{product.category}</span>
        </div>
      </div>

      <div className="product-card-role">{product.role}</div>

      <div className="product-card-coverage">
        {product.provider_satisfies_count > 0 && (
          <span className="cov-chip cov-provider">
            {product.provider_satisfies_count} provider
          </span>
        )}
        {product.shared_count > 0 && (
          <span className="cov-chip cov-shared">
            {product.shared_count} shared
          </span>
        )}
        {product.customer_owns_count > 0 && (
          <span className="cov-chip cov-customer">
            {product.customer_owns_count} customer
          </span>
        )}
      </div>

      <div className="product-card-footer">
        {product.is_active ? (
          <>
            <span className="product-active-label">
              Active — pending evidence
              {product.pinned_version_number != null && (
                <span className="product-version-badge"> · v{product.pinned_version_number}</span>
              )}
            </span>
            {!product.is_on_latest_version && canWrite && (
              <button
                className="btn-ghost btn-sm"
                onClick={handleMoveVersion}
                disabled={movingVersion}
                title={`A newer baseline version (v${product.current_version_number}) is available. Moving reviews only what actually changed -- see engine.py:move_org_product_version.`}
              >
                {movingVersion
                  ? "Moving…"
                  : `Move to v${product.current_version_number}`}
              </button>
            )}
            {canWrite && !asking && (
              <button
                className="btn-ghost btn-sm product-deactivate-btn"
                onClick={() => { setAsking(true); setReplacedBy(""); }}
              >
                Deactivate
              </button>
            )}
            {canWrite && asking && (
              // Jarrod, 2026-10-19: "Replaced by X" exists only when a
              // person names X here. Optional: blank is a real answer, and
              // the common one. Any product may be named; it need not cover
              // the same objectives, and the evidence says whether it does.
              <div className="product-deactivate-form" role="group" aria-label={`Deactivate ${product.name}`}>
                <label>
                  Replaced by{" "}
                  <select value={replacedBy} onChange={(e) => setReplacedBy(e.target.value)} disabled={deactivating}>
                    <option value="">Nothing — not replaced</option>
                    {others.map((o) => (
                      <option key={o.id} value={o.id}>{o.name}</option>
                    ))}
                  </select>
                </label>
                <span className="field-hint">
                  Optional. If you name one, its evidence here reads “Replaced by” it, with your name and
                  today’s date, on the objective and in the bundle. Left blank, it reads as archived, and an
                  objective left with no evidence says so.
                </span>
                <button className="btn-primary btn-sm" onClick={handleDeactivate} disabled={deactivating}>
                  {deactivating ? "Deactivating…" : "Deactivate"}
                </button>
                <button className="btn-ghost btn-sm" onClick={() => setAsking(false)} disabled={deactivating}>
                  Cancel
                </button>
              </div>
            )}
          </>
        ) : (
          canWrite && (
            <button
              className="btn-primary btn-sm"
              onClick={handleActivate}
              disabled={activating}
            >
              {activating ? "Activating…" : "Activate"}
            </button>
          )
        )}
        {error && <span className="product-card-error">{error}</span>}
      </div>
    </div>
  );
}
