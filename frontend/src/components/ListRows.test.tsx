// @vitest-environment jsdom
//
// The shared list table says when it is wider than the screen, and says
// nothing when it fits: the cue is measured, never assumed (L.8, L.9).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ListRowsTable } from "./ListRows";

const dims = (clientWidth: number, scrollWidth: number) => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => clientWidth });
  Object.defineProperty(HTMLElement.prototype, "scrollWidth", { configurable: true, get: () => scrollWidth });
};

afterEach(() => {
  cleanup();
  // Drop the overrides, so jsdom's own (zero) widths come back.
  Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
  Reflect.deleteProperty(HTMLElement.prototype, "scrollWidth");
});

function table() {
  return (
    <ListRowsTable title="Authorized Devices" rowCount={2} columnCount={19}>
      <tbody>
        <tr>
          <td>WS-0001</td>
        </tr>
      </tbody>
    </ListRowsTable>
  );
}

describe("ListRowsTable", () => {
  it("says a table wider than its box is wider, and which way the rest is", () => {
    dims(800, 2400);
    render(table());
    expect(screen.getByRole("note").textContent).toBe(
      "19 columns, wider than the screen. Scroll sideways for the rest →",
    );
    expect(screen.getByRole("region", { name: "Authorized Devices: 2 rows, 19 columns" }).className).toContain(
      "is-cut-right",
    );
  });

  it("says nothing when the table fits", () => {
    dims(800, 800);
    render(table());
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.getByRole("region").className).toBe("list-rows-scroll");
  });
});
