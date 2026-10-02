import { Activity, LayoutDashboard, LogOut, Radio, ScrollText, Settings2, Stethoscope } from "lucide-react";
import { ReactNode } from "react";
import { GlobalStats, SystemInfo } from "../lib/api";
import { LinkState } from "../lib/live";
import { Route } from "../lib/router";
import { NotificationBell } from "./NotificationBell";
import { ThemeToggle } from "./ThemeToggle";
import { StatusDot, cx } from "./ui";

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-accent-500 to-signal-500 shadow-lift">
        <svg viewBox="0 0 32 32" className="h-5 w-5"><path d="M7 12h14l-3.5-3.5M25 20H11l3.5 3.5" stroke="#fff" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </div>
      <div className="leading-tight">
        <div className="text-[15px] font-semibold tracking-tight text-ink-950">Portmirror</div>
        <div className="text-[11px] font-medium text-ink-400">Gateway control</div>
      </div>
    </div>
  );
}

const NAV = [
  { href: "#/", page: "dashboard", label: "Forwards", icon: LayoutDashboard },
  { href: "#/connections", page: "connections", label: "Connections", icon: Radio },
  { href: "#/audit", page: "audit", label: "Audit log", icon: ScrollText },
  { href: "#/diagnostics", page: "diagnostics", label: "Diagnostics", icon: Stethoscope },
  { href: "#/settings", page: "settings", label: "Settings", icon: Settings2 },
];

export function Shell({ route, link, global, system, user, onLogout, children }: {
  route: Route; link: LinkState; global?: GlobalStats; system: SystemInfo | null; user: string;
  onLogout: () => void; children: ReactNode;
}) {
  const active = route.page === "forward" ? "dashboard" : route.page;
  const vpn = global?.interfaces.vpn;
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-white/70 px-4 py-5 backdrop-blur-md lg:flex">
        <Logo />
        <nav className="mt-8 space-y-1">
          {NAV.map(({ href, page, label, icon: Icon }) => (
            <a key={page} href={href}
              className={cx("group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
                active === page ? "bg-gradient-to-r from-accent-50 to-transparent text-accent-600" : "text-ink-500 hover:bg-ink-950/[.035] hover:text-ink-900")}>
              <Icon size={17} className={active === page ? "text-accent-500" : "text-ink-400 group-hover:text-ink-700"} />
              {label}
              {active === page && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-accent-500" />}
            </a>
          ))}
        </nav>

        <div className="mt-auto space-y-3">
          <div className="rounded-xl border border-line bg-white p-3">
            <div className="label mb-2">Gateway</div>
            <div className="space-y-1.5 font-mono text-[11.5px]">
              <div className="flex items-center justify-between"><span className="text-ink-400">LAN</span><span className="text-ink-900">{system?.gw_lan_ip ?? "…"}</span></div>
              <div className="flex items-center justify-between"><span className="text-ink-400">VPN</span><span className="text-ink-900">{system?.gw_vpn_ip ?? "…"}</span></div>
              <div className="flex items-center justify-between">
                <span className="text-ink-400">{vpn?.name ?? "vpn"}</span>
                <span className="flex items-center gap-1.5 text-ink-900">
                  <StatusDot state={vpn?.state === "up" ? "up" : vpn ? "down" : "unknown"} />{vpn?.state ?? "…"}
                </span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 px-1">
            <div className="grid h-8 w-8 place-items-center rounded-full bg-ink-950/[.05] text-xs font-semibold uppercase text-ink-700">{user.slice(0, 2)}</div>
            <div className="min-w-0 flex-1 truncate text-sm font-medium text-ink-700">{user}</div>
            <button className="btn-ghost h-8 w-8 px-0" onClick={onLogout} title="Sign out" aria-label="Sign out"><LogOut size={16} /></button>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-white/70 px-5 backdrop-blur-md lg:px-8">
          <div className="lg:hidden"><Logo /></div>
          <nav className="flex min-w-0 gap-1 overflow-x-auto scroll-thin lg:hidden">
            {NAV.map(({ href, page, icon: Icon, label }) => (
              <a key={page} href={href} aria-label={label} className={cx("btn-ghost h-8 w-8 px-0", active === page && "text-accent-600")}><Icon size={16} /></a>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-4 text-xs">
            {global && (
              <span className="hidden items-center gap-1.5 text-ink-500 sm:flex">
                <Activity size={14} className="text-signal-500" />
                <span className="num text-ink-900">{global.forwards_active}</span>/<span className="num">{global.forwards_total}</span> forwards active
              </span>
            )}
            <span className={cx("flex items-center gap-2 rounded-full border px-2.5 py-1 font-medium",
              link === "live" ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : link === "connecting" ? "border-line bg-white text-ink-500" : "border-rose-200 bg-rose-50 text-rose-700")}>
              <StatusDot state={link === "live" ? "up" : link === "connecting" ? "warn" : "down"} pulse={link === "live"} />
              {link === "live" ? "Live" : link === "connecting" ? "Connecting" : "Reconnecting"}
            </span>
            <ThemeToggle />
            <NotificationBell />
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-5 py-6 lg:px-8 lg:py-8">{children}</main>
      </div>
    </div>
  );
}
