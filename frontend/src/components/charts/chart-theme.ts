// Recharts wants real CSS color strings, not Tailwind class names -- these
// reference the same CSS custom properties shadcn's theme already defines
// (see app/globals.css's --chart-1..5, --border, --muted-foreground), so
// charts automatically stay correct in both light and dark mode without
// any separate chart-specific palette.
export const CHART_COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];
export const AXIS_COLOR = "var(--muted-foreground)";
export const GRID_COLOR = "var(--border)";
export const TOOLTIP_STYLE = {
  backgroundColor: "var(--popover)",
  color: "var(--popover-foreground)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-md)",
  fontSize: 12,
  padding: "8px 10px",
};
