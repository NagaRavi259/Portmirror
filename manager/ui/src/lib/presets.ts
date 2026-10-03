import type { Protocol } from "./api";

/** Common services, offered as a starting point for a new forward. A preset only fills in the
 * protocol and local port - never the remote target, which is always specific to your own network. */
export interface ServicePreset { id: string; label: string; protocol: Protocol; port: number }

export const SERVICE_PRESETS: ServicePreset[] = [
  { id: "rdp", label: "RDP (remote desktop)", protocol: "tcp", port: 3389 },
  { id: "vnc", label: "VNC (remote desktop)", protocol: "tcp", port: 5900 },
  { id: "ssh", label: "SSH", protocol: "tcp", port: 22 },
  { id: "jellyfin", label: "Jellyfin", protocol: "tcp", port: 8096 },
  { id: "pihole", label: "Pi-hole admin", protocol: "tcp", port: 80 },
  { id: "homeassistant", label: "Home Assistant", protocol: "tcp", port: 8123 },
  { id: "plex", label: "Plex", protocol: "tcp", port: 32400 },
  { id: "minecraft", label: "Minecraft", protocol: "tcp", port: 25565 },
];
