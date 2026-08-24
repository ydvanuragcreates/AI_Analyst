import { render, screen } from "@testing-library/react";
import { Database } from "lucide-react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "@/components/common/empty-state";

describe("EmptyState", () => {
  it("renders the title, description, and optional action", () => {
    render(
      <EmptyState
        icon={Database}
        title="No datasets yet"
        description="Upload your first dataset to start analyzing it."
        action={<button>Upload dataset</button>}
      />
    );

    expect(screen.getByText("No datasets yet")).toBeInTheDocument();
    expect(screen.getByText("Upload your first dataset to start analyzing it.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload dataset" })).toBeInTheDocument();
  });

  it("renders without an action when none is provided", () => {
    render(<EmptyState icon={Database} title="No datasets" description="Nothing here." />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
