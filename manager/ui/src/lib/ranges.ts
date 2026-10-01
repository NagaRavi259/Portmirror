export type Range = "live" | "1h" | "24h" | "7d" | "30d" | "1y" | "all";

/** "live" = sliding 5-minute window at 1 s resolution (new data enters on the right). */
export const LIVE_WINDOW_S = 300;

export const RANGES: { value: Range; label: string }[] = [
  { value: "live", label: "Live" }, { value: "1h", label: "1h" }, { value: "24h", label: "24h" },
  { value: "7d", label: "7d" }, { value: "30d", label: "30d" }, { value: "1y", label: "1y" }, { value: "all", label: "All" },
];
