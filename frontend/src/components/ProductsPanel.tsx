import { useEffect, useState } from "react";
import { api } from "../api";
import type { ProductRow } from "../types";
import { ProductCard } from "./ProductCard";

interface Props {
  orgId: string;
  assessmentId: string;
  onClose: () => void;
  onActivated: () => void;
  onDeactivated: () => void;
}

export function ProductsPanel({ orgId, assessmentId, onClose, onActivated, onDeactivated }: Props) {
  const [products, setProducts] = useState<ProductRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    api
      .getProducts(orgId, assessmentId)
      .then(setProducts)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, [orgId, assessmentId]);

  function handleActivated() {
    load();
    onActivated();
  }

  function handleDeactivated() {
    load();
    onDeactivated();
  }

  return (
    <div className="panel-overlay" onClick={onClose}>
      <aside className="products-panel" onClick={(e) => e.stopPropagation()}>
        <div className="products-panel-header">
          <span className="products-panel-title">Security Tools</span>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            &#x2715;
          </button>
        </div>
        <div className="products-panel-subtitle">
          Activate a tool to pre-populate covered objectives as pending evidence.
        </div>

        {loading ? (
          <div className="loading">Loading products&#x2026;</div>
        ) : error ? (
          <div className="error-msg">{error}</div>
        ) : products.length === 0 ? (
          <div className="empty">
            No products in the baseline library for this framework.
          </div>
        ) : (
          <div className="products-list">
            {products.map((p) => (
              <ProductCard
                key={p.id}
                product={p}
                orgId={orgId}
                assessmentId={assessmentId}
                onActivated={handleActivated}
                onDeactivated={handleDeactivated}
              />
            ))}
          </div>
        )}
      </aside>
    </div>
  );
}
