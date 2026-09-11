import type { ReactNode } from "react";

// Shared presentational primitives for every side nav in this app --
// SideNav.tsx (per-org) and AdminArea.tsx (deployment-tier) render
// visually and behaviorally identical navs by construction, not by
// copying class names into two places that can drift. No business logic
// here: category/active-state decisions stay in each caller.

export function SideNavRoot({ children }: { children: ReactNode }) {
  return <nav className="side-nav">{children}</nav>;
}

export function SideNavCategory({ children }: { children: ReactNode }) {
  return <div className="side-nav-category">{children}</div>;
}

export function SideNavItem({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button className={`side-nav-item${active ? " active" : ""}`} onClick={onClick}>
      {children}
    </button>
  );
}

export function SideNavSubitems({ children }: { children: ReactNode }) {
  return <div className="side-nav-subitems">{children}</div>;
}

export function SideNavSubitem({
  active,
  onClick,
  disabled = false,
  children,
}: {
  active: boolean;
  onClick?: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      className={`side-nav-subitem${active ? " active" : ""}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
