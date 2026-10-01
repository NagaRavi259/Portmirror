export type Protocol = "tcp" | "udp" | "both";

export interface Health {
  state: "up" | "down";
  latency_ms: number | null;
  checked_at: number;
  method: string;
  error: string | null;
}

export interface ForwardStats {
  id: number;
  new_total: number;
  bytes_in_total: number;
  bytes_out_total: number;
  pkts_in_total: number;
  pkts_out_total: number;
  rate_limited_total: number;
  conn_limited_total: number;
  new_per_s: number;
  in_bps: number;
  out_bps: number;
  live: number;
  flows: number;
  top_clients: { ip: string; conns: number; bytes: number }[];
  health: Health | null;
}

export interface ForwardBody {
  name: string;
  protocol: Protocol;
  listen_port: number;
  listen_port_end: number | null;
  target_ip: string;
  target_port: number;
  allowed_sources: string[];
  rate_limit: number | null;
  max_conns: number | null;
  expires_at: string | null;
  enabled: boolean;
  description: string;
}

export interface Forward extends ForwardBody {
  id: number;
  created_at: string;
  updated_at: string;
  target_port_end: number | null;
  expired: boolean;
  stats: ForwardStats | null;
  health: Health | null;
  spark?: [number, number, number][];
}

export interface Iface { name: string; state: string; rx_bps: number; tx_bps: number; rx_total?: number; tx_total?: number }

export interface GlobalStats {
  live: number;
  new_per_s: number;
  in_bps: number;
  out_bps: number;
  forwards_total: number;
  forwards_active: number;
  conntrack_count: number;
  conntrack_max: number;
  interfaces: { lan: Iface; vpn: Iface };
  system: { cpu_pct: number; mem_bytes: number; mem_limit: number; load: number };
  uptime_s: number;
  kernel_table: boolean;
  version: number;
}

export interface Snapshot {
  ts: number;
  forwards: Record<string, ForwardStats>;
  global: GlobalStats;
  spark?: Record<string, [number, number, number][]>;
}

export interface Conn {
  proto: "tcp" | "udp";
  state: string;
  ttl: number;
  src: string;
  dst: string;
  sport: number;
  dport: number;
  reply_src: string;
  reply_sport: number;
  pkts_in: number;
  bytes_in: number;
  pkts_out: number;
  bytes_out: number;
  assured: boolean;
}

export interface HistoryPoint { t: number; new_per_s: number; in_bps: number; out_bps: number; live: number }
export interface History { range: string; step_s: number; points: HistoryPoint[] }

export interface AuditEntry { id: number; ts: string; actor: string; action: string; target: string | null; detail: unknown }
export interface TokenRow { id: number; name: string; created_at: string; last_used: string | null }
export interface SystemInfo {
  lan_if: string; vpn_if: string; lan_net: string; vpn_net: string; gw_lan_ip: string; gw_vpn_ip: string;
  ui_port: number; reserved_tcp_ports: number[]; max_range: number; last_apply: string | null; hold: boolean;
  history_retention_days: number | null; audit_retention_days: number | null; db_bytes: number;
  storage: { rollups: number; oldest_rollup: number | null; audit: number; oldest_audit: string | null };
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public fields: Record<string, string> = {}) {
    super(message);
  }
}

type Detail = string | { loc: (string | number)[]; msg: string }[];

function parseDetail(detail: Detail | undefined, status: number): ApiError {
  if (Array.isArray(detail)) {
    const fields: Record<string, string> = {};
    const msgs: string[] = [];
    for (const d of detail) {
      const key = d.loc.filter((x) => x !== "body").join(".");
      const msg = d.msg.replace(/^Value error, /, "");
      if (key) fields[key] = msg;
      msgs.push(key ? `${key}: ${msg}` : msg);
    }
    return new ApiError(status, msgs.join("; "), fields);
  }
  return new ApiError(status, detail || `Request failed (${status})`);
}

export let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export async function api<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && !path.startsWith("/api/auth/login")) onUnauthorized();
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw parseDetail(data?.detail, res.status);
  return data as T;
}

export const Api = {
  me: () => api<{ username: string; must_change_password: boolean }>("GET", "/api/auth/me"),
  login: (username: string, password: string) =>
    api<{ username: string; must_change_password: boolean }>("POST", "/api/auth/login", { username, password }),
  logout: () => api("POST", "/api/auth/logout"),
  changePassword: (current: string, next: string) => api("POST", "/api/auth/password", { current, new: next }),
  forwards: () => api<Forward[]>("GET", "/api/forwards"),
  forward: (id: number) => api<Forward>("GET", `/api/forwards/${id}`),
  create: (b: ForwardBody) => api<Forward>("POST", "/api/forwards", b),
  update: (id: number, b: ForwardBody, kill = false) => api<Forward>("PUT", `/api/forwards/${id}?kill=${kill}`, b),
  remove: (id: number, kill: boolean) => api<{ killed: number }>("DELETE", `/api/forwards/${id}?kill=${kill}`),
  toggle: (id: number, enabled: boolean, kill = false) => api<Forward>("POST", `/api/forwards/${id}/toggle`, { enabled, kill }),
  connections: (id: number) => api<{ total: number; connections: Conn[] }>("GET", `/api/forwards/${id}/connections`),
  killAll: (id: number) => api<{ killed: number }>("POST", `/api/forwards/${id}/kill`),
  killOne: (c: Conn) => api("POST", "/api/connections/kill", { protocol: c.proto, src: c.src, sport: c.sport, dport: c.dport }),
  history: (id: number, range: string) => api<History>("GET", `/api/forwards/${id}/history?range=${range}`),
  globalHistory: (range: string) => api<History>("GET", `/api/history?range=${range}`),
  audit: (limit = 200, before?: number) => api<AuditEntry[]>("GET", `/api/audit?limit=${limit}${before ? `&before=${before}` : ""}`),
  exportAll: () => api<{ forwards: ForwardBody[] }>("GET", "/api/export"),
  importAll: (forwards: ForwardBody[], mode: "merge" | "replace") =>
    api<{ imported: number }>("POST", "/api/import", { forwards, mode }),
  tokens: () => api<TokenRow[]>("GET", "/api/tokens"),
  createToken: (name: string) => api<{ id: number; name: string; token: string }>("POST", "/api/tokens", { name }),
  deleteToken: (id: number) => api("DELETE", `/api/tokens/${id}`),
  system: () => api<SystemInfo>("GET", "/api/system"),
};
