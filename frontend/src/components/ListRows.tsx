/**
 * The table a list's rows are drawn in: a library list's page (L.8) and the
 * four lists drawn from scope (L.9) both use this, so the Lists index opens
 * one kind of table whichever entry is clicked. Its style is `.list-rows`
 * in styles.css. Only the frame is shared -- each caller draws its own
 * header and cells.
 */

import { useEffect, useState, type ReactNode } from "react";

/** Whether a wide table has columns off either side, so the page can say
 * so: a scrollbar alone hid the device list's last columns from everyone. */
export function useSideScroll() {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const [edge, setEdge] = useState({ left: false, right: false });
  useEffect(() => {
    if (!el) return;
    const update = () => {
      const left = el.scrollLeft > 0;
      const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
      setEdge((e) => (e.left === left && e.right === right ? e : { left, right }));
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    ro?.observe(el);
    if (el.firstElementChild) ro?.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", update);
      ro?.disconnect();
    };
  }, [el]);
  return [setEl, edge] as const;
}

export function ListRowsTable({
  title,
  rowCount,
  columnCount,
  children,
}: {
  title: string;
  rowCount: number;
  columnCount: number;
  children: ReactNode;
}) {
  const [setScroller, sideways] = useSideScroll();
  return (
    <>
      {(sideways.left || sideways.right) && (
        <p className="list-scroll-hint" role="note">
          {columnCount} columns, wider than the screen.{" "}
          {sideways.right ? "Scroll sideways for the rest →" : "← Scroll back for the first columns"}
        </p>
      )}
      <div
        ref={setScroller}
        className={`list-rows-scroll${sideways.left ? " is-cut-left" : ""}${sideways.right ? " is-cut-right" : ""}`}
        tabIndex={0}
        role="region"
        aria-label={`${title}: ${rowCount} rows, ${columnCount} columns`}
      >
        <table className="contacts-table list-rows">{children}</table>
      </div>
    </>
  );
}
