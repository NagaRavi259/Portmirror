import { useEffect, useState } from "react";

export type Route =
  | { page: "dashboard" }
  | { page: "forward"; id: number }
  | { page: "connections" }
  | { page: "audit" }
  | { page: "diagnostics" }
  | { page: "settings" };

function parse(hash: string): Route {
  const h = hash.replace(/^#\/?/, "");
  const m = h.match(/^forwards\/(\d+)/);
  if (m) return { page: "forward", id: Number(m[1]) };
  if (h.startsWith("connections")) return { page: "connections" };
  if (h.startsWith("audit")) return { page: "audit" };
  if (h.startsWith("diagnostics")) return { page: "diagnostics" };
  if (h.startsWith("settings")) return { page: "settings" };
  return { page: "dashboard" };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(location.hash));
  useEffect(() => {
    const on = () => setRoute(parse(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function go(path: string) {
  location.hash = path.startsWith("#") ? path : `#/${path.replace(/^\//, "")}`;
}
