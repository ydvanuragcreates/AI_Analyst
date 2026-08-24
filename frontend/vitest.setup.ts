import "@testing-library/jest-dom/vitest";

// jsdom has no ResizeObserver -- Recharts' <ResponsiveContainer> needs one
// to measure its container. A no-op polyfill is the standard way to test
// Recharts-based components under jsdom (there's no real layout to observe).
class ResizeObserverPolyfill {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = globalThis.ResizeObserver ?? ResizeObserverPolyfill;
