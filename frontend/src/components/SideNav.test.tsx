// @vitest-environment jsdom
//
// The Library's Baselines entry is the org-level `baseline` document
// filter, and the Lists entry points at Scope > Lists. The product baseline
// library is AdminArea's "Product Baselines" -- a different feature that
// once shared this word, which is what sent Baselines to the wrong place.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SideNav } from "./SideNav";

afterEach(cleanup);

function renderLibrary() {
  const props = {
    category: "library" as const,
    onSelectCategory: vi.fn(),
    scopeTab: "profile" as const,
    onSelectScopeTab: vi.fn(),
    onFocusSystemSection: vi.fn(),
    assessmentsTab: "board" as const,
    onSelectAssessmentsTab: vi.fn(),
    securityTab: "users" as const,
    onSelectSecurityTab: vi.fn(),
    libraryTab: "all" as const,
    onSelectLibraryTab: vi.fn(),
    currentUserRole: "msp_engineer",
    status: null,
  };
  render(<SideNav {...props} />);
  return props;
}

describe("SideNav Library", () => {
  it("Baselines filters the document library to the baseline type", () => {
    const props = renderLibrary();
    const entry = screen.getByRole("button", { name: "Baselines" });
    expect((entry as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(entry);
    expect(props.onSelectLibraryTab).toHaveBeenCalledWith("baseline");
  });

  it("does not use the product baseline library's name anywhere near it", () => {
    renderLibrary();
    expect(screen.queryByText(/Product Baseline/)).toBeNull();
  });

  it("Lists says where it goes and goes there", () => {
    const props = renderLibrary();
    const entry = screen.getByRole("button", { name: /Lists/ });
    expect(entry.getAttribute("title")).toMatch(/Scope › Lists/);
    fireEvent.click(entry);
    expect(props.onSelectCategory).toHaveBeenCalledWith("scope");
    expect(props.onSelectScopeTab).toHaveBeenCalledWith("lists");
  });
});
