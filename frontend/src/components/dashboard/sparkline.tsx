import { useId } from "react";

/** Tiny area sparkline. Purely decorative next to the number it sits
 * beside, so it's hidden from assistive tech. */
export function Sparkline({ values, color, className }: { values: number[]; color: string; className?: string }) {
  const gradientId = useId();
  const width = 96;
  const height = 36;
  const pad = 3;

  const points = values.length > 1 ? values : [0, 0];
  const max = Math.max(...points);
  const min = Math.min(...points);
  const range = max - min || 1;
  const step = (width - pad * 2) / (points.length - 1);
  const coords = points.map((v, i) => [pad + i * step, height - pad - ((v - min) / range) * (height - pad * 2)] as const);

  // Smooth the line with simple midpoint quadratic curves.
  let line = `M ${coords[0][0]} ${coords[0][1]}`;
  for (let i = 1; i < coords.length; i++) {
    const [px, py] = coords[i - 1];
    const [x, y] = coords[i];
    const mx = (px + x) / 2;
    line += ` Q ${px} ${py} ${mx} ${(py + y) / 2} T ${x} ${y}`;
  }
  const area = `${line} L ${coords[coords.length - 1][0]} ${height} L ${coords[0][0]} ${height} Z`;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className={className} aria-hidden="true" preserveAspectRatio="none">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.35} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradientId})`} />
      <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
