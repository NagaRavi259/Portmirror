# Installation: Raspberry Pi (native, no Docker)

This runs the exact same manager application as the Docker setup, but as a plain Python service under
systemd — no containers on the Pi at all. It's the right choice for an actual always-on deployment: a Pi has
limited resources and doesn't need the Docker test topology, which exists only to validate the design, not to
run in production.

Every network setting the application needs is read from environment variables, so this is the same code
either way — only how it's launched differs.

## 1. Prerequisites

- Raspberry Pi OS, 64-bit recommended for the widest package-wheel availability
- A VPN connection already working on the Pi, with a real route to the remote network (WireGuard, OpenVPN, or
  Tailscale with subnet routing — see the Tailscale note below if that's your setup)
- A second machine with Docker, used once to build the web UI (the Pi itself never needs Node.js)

### If your VPN is Tailscale

Tailscale substitutes directly for WireGuard here with no code changes — the gateway only cares about an
interface name and a reachable subnet, not which VPN software provides them. Two things to confirm before
continuing:

```bash
ip route get <an address on the remote subnet>
```
must show `dev tailscale0`. If it doesn't, the remote side needs to advertise that subnet as a Tailscale route
(`sudo tailscale up --advertise-routes=<subnet>` on that machine), and the route needs approving in the
Tailscale admin console. Also confirm this Pi is actually accepting routes (`tailscale status` shouldn't warn
about routes being advertised but not accepted; if it does, `sudo tailscale set --accept-routes=true`).

The service unit name to use below is `tailscaled.service`, not `wg-quick@wg0.service`.

## 2. Confirm your real network

```bash
ip -br link                      # the LAN NIC and the tunnel interface
ip -br addr                      # confirm the addresses already assigned to each
systemctl list-units --type=service --all | grep -Ei 'wg-quick|openvpn|wireguard|tailscaled'
```

Before copying anything to the Pi, edit on your own machine:
- `deploy-pi/nftables.conf`: `LAN_IF`, `VPN_IF`
- `deploy-pi/portmirror.env.example`: `PM_LAN_IF`, `PM_VPN_IF`, `PM_LAN_NET`, `PM_VPN_NET`, `PM_GW_LAN_IP`,
  `PM_GW_VPN_IP` — keep these in sync with the two values above; the application validates new forwards
  against `PM_VPN_NET` independently of the firewall file, so a mismatch here means the *filter* is wrong, not
  that the application breaks
- `deploy-pi/portmirror-base.service` / `portmirror-manager.service`: replace `wg-quick@wg0.service` with your
  real VPN unit name if it isn't WireGuard

**Before doing anything else, make sure your local LAN subnet is genuinely different from the remote
subnet.** If they're the same, routing breaks regardless of any VPN or firewall configuration — a directly
connected local subnet always takes priority over a routed one for the same address range, so the gateway
would never be able to reach anything on the "remote" side at all.

## 3. Build the UI

On the separate machine with Docker (not the Pi):
```bash
bash deploy-pi/build_ui_bundle.sh
```
This produces `manager/ui/dist/`, a small (under 1 MB) set of static files.

## 4. Copy everything to the Pi and install

```bash
rsync -az --exclude manager/ui/node_modules --exclude tests/results \
      /path/to/this/repo/ pi@<pi-ip>:/opt/portmirror-src/
ssh pi@<pi-ip>
cd /opt/portmirror-src/deploy-pi
sudo ./install.sh
```

`install.sh`:
1. Shows the Pi's real interfaces and VPN-capable services, and asks you to confirm they match what you
   configured in step 2.
2. Installs the needed packages (`nftables`, `conntrack`, `python3-venv`, and a C compiler in case a Python
   dependency needs to build from source).
3. Copies the application and built UI into `/opt/portmirror`, creates a Python virtual environment, and
   installs dependencies into it.
4. Installs the firewall ruleset and environment file into `/etc/portmirror/` — an existing
   `/etc/portmirror/portmirror.env` is never overwritten, so re-running `install.sh` later (after a `git pull`,
   say) won't clobber your configuration.
5. Syntax-checks the ruleset before touching the kernel.
6. Installs and starts the two systemd services.

It's safe to re-run after editing `nftables.conf` or `portmirror.env` — follow up with
`sudo systemctl restart portmirror-base portmirror-manager` to pick up the change.

## 5. First login

```bash
pmctl password        # the generated admin password, shown once
pmctl forwards         # list configured forwards
pmctl status           # a quick health summary
```

Open `http://<pi-lan-ip>:8088` from a device on your LAN, sign in as `admin`, and change the password. No
forwards are pre-configured — add your first one through the dashboard. The firewall blocks the dashboard's
port from the VPN side, so it's reachable only from your LAN, the same way a router's own admin page would be.

## 6. Verify

```bash
sudo systemctl status portmirror-base portmirror-manager
sudo nft list ruleset          # the static tables, plus the dynamic one once a forward exists
journalctl -u portmirror-manager -f
```

Check masquerade from the target's own point of view too — it should see the gateway's VPN-side address, never
the real local client's.

## Updating later

```bash
pmctl update
```

Checks GitHub for a newer release, shows what's new, and asks before installing it. See
[`updates.md`](updates.md) for the full flow and how releases get cut in the first place.

## Uninstall

```bash
sudo systemctl disable --now portmirror-manager portmirror-base
sudo rm /etc/systemd/system/portmirror-{base,manager}.service
sudo rm -rf /opt/portmirror /etc/portmirror /var/lib/portmirror /etc/sysctl.d/99-portmirror.conf
sudo rm /usr/local/bin/pmctl
sudo systemctl daemon-reload
```
