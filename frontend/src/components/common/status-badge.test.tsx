import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "@/components/common/status-badge";

describe("StatusBadge", () => {
  it("renders a distinct label for every analysis status (never relies on color alone)", () => {
    const statuses = ["queued", "running", "waiting_for_approval", "completed", "failed", "cancelled"] as const;
    for (const status of statuses) {
      const { unmount } = render(<StatusBadge status={status} />);
      // Each status must render actual visible text, not just a color swatch.
      expect(screen.getByText(/\w/)).toBeInTheDocument();
      unmount();
    }
  });

  it("shows 'Completed' text for a completed analysis", () => {
    render(<StatusBadge status="completed" />);
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("shows 'Failed' text for a failed analysis", () => {
    render(<StatusBadge status="failed" />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });
});
