// @vitest-environment jsdom
//
// The Library's Baselines entry is the org-level `baseline` document
// filter, and Lists is the one list screen (L.2). The product baseline
// library is AdminArea's "Product Baselines" -- a different feature that
// once shared this word, which is what sent Baselines to the wrong place.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SideNav } from "./SideNav";

afterEach(cleanup);

function renderNav(category: "library" | "scope") {
  const props = {
    category,
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

const renderLibrary = () => renderNav("library");

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

  it("Lists opens the list library, in the Library", () => {
    const props = renderLibrary();
    fireEvent.click(screen.getByRole("button", { name: "Lists" }));
    expect(props.onSelectLibraryTab).toHaveBeenCalledWith("lists");
    expect(props.onSelectCategory).not.toHaveBeenCalled();
  });

  it("is the only place called Lists: Scope no longer has one", () => {
    renderNav("scope");
    expect(screen.queryByRole("button", { name: /Lists/ })).toBeNull();
  });
});
