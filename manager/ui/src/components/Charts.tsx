import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";

/** Tiny SVG area sparkline. */
export function Sparkline({ values, width = 120, height = 32, color = "#4f46e5" }: {
  values: number[]; width?: number; height?: number; color?: string;
}) {
  const id = useMemo(() => `sg${Math.random().toString(36).slice(2)}`, []);
  if (values.length < 2) {
    return <svg width={width} height={height}><line x1="0" x2={width} y1={height - 1} y2={height - 1} stroke="#e6e9f2" /></svg>;
  }
  const max = Math.max(...values, 1);
  const step = width / (values.length - 1);
  const pts = values.map((v, i) => [i * step, height - 2 - (v / max) * (height - 5)]);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  return (
    <svg width={width} height={height} className="overflow-visible">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity=".22" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line}L${width},${height}L0,${height}Z`} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export interface SeriesDef { label: string; color: string; fill?: boolean; fmt: (v: number) => string }

/** Responsive uPlot time-series chart. data = [timestamps(s), ...series]. */
export function TimeChart({ data, series, height = 220, yFmt, windowS }: {
  data: number[][]; series: SeriesDef[]; height?: number; yFmt: (v: number) => string;
  /** fixed-width sliding window (seconds): x axis always shows [latest - windowS, latest] */
  windowS?: number;
}) {
  const el = useRef<HTMLDivElement>(null);
  const win = useRef(windowS);
  win.current = windowS;
  const plot = useRef<uPlot | null>(null);
  const tip = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!el.current) return;
    const opts: uPlot.Options = {
      width: el.current.clientWidth,
      height,
      padding: [12, 8, 0, 0],
      cursor: { points: { size: 7, fill: "#fff", width: 2 }, drag: { x: false, y: false } },
      legend: { show: false },
      scales: { x: { time: true, range: (_u, min, max) => (win.current ? [max - win.current, max] : [min, max]) }, y: { range: (_u, _min, max) => [0, max > 0 ? max * 1.15 : 1] } },
      axes: [
        { stroke: "#8792ad", grid: { show: false }, ticks: { stroke: "#e6e9f2", width: 1, size: 4 }, font: "11px Inter Variable, sans-serif",
          space: 70, incrs: [1, 5, 10, 15, 30, 60, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400, 172800] },
        {
          stroke: "#8792ad", size: 64, font: "11px JetBrains Mono, monospace",
          grid: { stroke: "#eef0f6", width: 1 }, ticks: { show: false },
          values: (_u, vals) => vals.map((v) => yFmt(v)),
        },
      ],
      series: [
        {},
        ...series.map((s) => ({
          label: s.label, stroke: s.color, width: 2,
          fill: s.fill ? (u: uPlot) => {
            const g = u.ctx.createLinearGradient(0, u.bbox.top, 0, u.bbox.top + u.bbox.height);
            g.addColorStop(0, s.color + "33");
            g.addColorStop(1, s.color + "00");
            return g;
          } : undefined,
          points: { show: false },
        })),
      ],
      hooks: {
        setCursor: [(u) => {
          const t = tip.current;
          if (!t) return;
          const i = u.cursor.idx;
          if (i == null || u.cursor.left == null || u.cursor.left < 0) { t.style.opacity = "0"; return; }
          const ts = new Date((u.data[0][i] as number) * 1000).toLocaleTimeString();
          t.innerHTML = `<div class="text-[11px] text-ink-400 mb-1">${ts}</div>` + series.map((s, k) =>
            `<div class="flex items-center gap-2 text-xs"><span class="h-2 w-2 rounded-full" style="background:${s.color}"></span>` +
            `<span class="text-ink-500">${s.label}</span><span class="ml-auto pl-3 font-mono text-ink-900">${s.fmt((u.data[k + 1][i] as number) ?? 0)}</span></div>`).join("");
          const left = Math.min(u.cursor.left + 70, u.over.clientWidth - 120);
          t.style.transform = `translate(${left}px, 8px)`;
          t.style.opacity = "1";
        }],
      },
    };
    plot.current = new uPlot(opts, data as uPlot.AlignedData, el.current);
    const ro = new ResizeObserver(() => el.current && plot.current?.setSize({ width: el.current.clientWidth, height }));
    ro.observe(el.current);
    return () => { ro.disconnect(); plot.current?.destroy(); plot.current = null; };
    // re-create only when the series layout changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series.map((s) => s.label + s.color).join(), height]);

  useEffect(() => {
    const u = plot.current;
    if (!u) return;
    u.setData(data as uPlot.AlignedData);
    // expose the visible x range (used by the browser tests to check the live window)
    if (el.current) {
      el.current.dataset.xmin = String(u.scales.x.min ?? "");
      el.current.dataset.xmax = String(u.scales.x.max ?? "");
    }
  }, [data]);

  return (
    <div className="relative">
      <div ref={el} className="w-full" data-testid="timechart" />
      <div ref={tip} className="pointer-events-none absolute left-0 top-0 min-w-[160px] rounded-lg border border-line bg-white/95 px-3 py-2 opacity-0 shadow-lift transition-opacity" />
    </div>
  );
}
