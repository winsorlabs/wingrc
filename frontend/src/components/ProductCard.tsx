import { useState } from "react";
import { api } from "../api";
import type { ProductRow } from "../types";

interface Props {
  product: ProductRow;
  orgId: string;
  assessmentId: string;
  onActivated: () => void;
}

export function ProductCard({ product, orgId, assessmentId, onActivated }: Props) {
  const [activating, setActivating] = useState(false);
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
          <span className="product-active-label">Active — pending evidence</span>
        ) : (
          <button
            className="btn-primary btn-sm"
            onClick={handleActivate}
            disabled={activating}
          >
            {activating ? "Activating…" : "Activate"}
          </button>
        )}
        {error && <span className="product-card-error">{error}</span>}
      </div>
    </div>
  );
}
