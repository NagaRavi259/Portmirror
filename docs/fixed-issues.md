# Fixed issues

A running record of real problems found during development and real deployment, and how each was fixed. Kept
for anyone touching this code later — several of these are easy to reintroduce by accident, and a few are
exactly the kind of thing that only shows up once something runs on real, varied hardware.

## Application bugs

| Problem | Cause | Fix |
|---|---|---|
| Every rule rebuild would fail on some real `nftables` builds with `Error: Not a regular file: "/dev/stdin"` | The management process's event loop runs a subprocess's standard input as a socketpair, not a plain pipe. Some `nftables` versions refuse to read a script from `-f -` when standard input isn't a regular file; others tolerate it, which is why this went unnoticed until it ran on different hardware | The generated script is written to a real temporary file and `nft -f` is pointed at its path, instead of piping it through standard input, on every call |
| A forward's target's VPN-interface status always showed as "unknown," including for a link that was demonstrably working | Interface status was read from `operstate` alone. TUN-style interfaces (the kind a VPN client like Tailscale creates) never implement the signalling that field depends on, so it reports "unknown" permanently regardless of real state — confirmed directly against a live interface that had carried real traffic | Falls back to the kernel's `carrier` flag only when `operstate` itself has nothing to say; real network interfaces, which already report a correct `operstate`, are unaffected |
| The command-line tool crashed with a raw permission-denied traceback when run without elevated privileges | Its internal authentication token is deliberately restricted to the management process's own account — correct, since another account on the same machine reading it would get full administrative access — but the tool never elevated itself or said why it failed | The tool re-elevates itself automatically when not already running with sufficient privilege; the underlying code also catches the permission error directly and prints a plain message instead of a traceback, in case anything bypasses the wrapper |
| Cutting a single connection didn't actually end it; the connection just continued | Removing a connection's kernel tracking entry while its forward stays enabled isn't enough — the very next packet is matched by the same rule and reprocessed exactly as before, and the connection resumes | A short-lived blocklist is checked first: a cut connection's next packet gets rejected (or dropped, for UDP) before its tracking entry is removed |
| Cutting connections on a busy forward could take ten seconds or more, and reported killing far more connections than were actually open | One external process was run per connection, including ones that had already closed on their own | Connections for a forward are blocked in a single batched operation and its tracking entries cleared with one call per port, filtered by target; only connections that were actually still open are counted |
| A firewall rule intended to block unmapped traffic silently dropped every legitimate connection instead | The rule compared a connection's full tracking-status value for inequality against one flag; a connection created by this system's own address-translation rule actually carries two flags at once, so the comparison never matched what was intended | Rewritten as a positive match (accept known-translated connections, then drop anything new) rather than a negative one — caught by testing actual traffic through the rule, not by syntax-checking it |
| A dashboard page rendered completely blank in some environments | A charting library built part of its output using a locale value that a non-standard environment reported in a form the library's number-formatting code rejected outright | The application falls back to a known-good locale whenever the reported one is invalid, before anything else runs |
| The main live-traffic chart stopped looking live after the process had been running a while | The chart simply fit all available history into its fixed width, so as more history accumulated the apparent "live" motion from early on was really just a mostly-empty window filling up | A dedicated live view holds a genuinely fixed, sliding time window regardless of how much history exists, independent of the longer-range historical views |
| A background data pipeline occasionally crashed with a "collection changed size during iteration" error | A set of active connections was iterated directly while connections were being added and removed from it concurrently | Iterate over a snapshot of the set instead of the live set itself |
| A background worker crashed on startup if a dependent service hadn't finished initializing yet | A single connection attempt was made with no retry | A bounded retry loop, plus automatic reconnection if the connection is later lost |
| A value from an environment variable was interpolated directly into a query string | Convenient during early development, but a configuration value shouldn't be allowed to shape a query's structure | Rebuilt using the database driver's own identifier-quoting support instead of string interpolation |
| A security check meant to catch traffic leaking from an untrusted source occasionally reported a false alarm | The check watched for *any* packet reaching a backend from a capture point that also saw the system's own routine health checks, which are deliberately translated the same way real traffic is | Rewritten to check the forwarding rule's own hit counters and connection tracking directly, rather than inferring intent from a packet capture; a deliberately reintroduced real leak was used to confirm the rewritten check still actually catches one |
| A specific destination for a web service timed out under heavy concurrent load, independent of anything the gateway was doing | A single-threaded development server serialized every incoming TLS handshake, becoming a bottleneck of its own under load | Replaced with a server that handles connections concurrently; confirmed by running the same load directly against the backend, bypassing the gateway entirely, and seeing the same failures |

## Testing and tooling notes

Lower-severity items, specific to the development and test environment rather than the application itself, but
worth keeping in mind since a few of them are easy to hit again:

- **A shell pattern-match command that includes its own search pattern can match and terminate itself** —
  relevant to anything built on `pkill -f` or similar; a more specific pattern, or matching by process ID
  directly, avoids it.
- **A background process started without its own session can still be terminated by cleanup of its parent's
  process group**, even after being detached with `nohup`; starting it in its own session avoids this.
- **A networking tool's "quit after end-of-input" option can fire almost immediately in a backgrounded
  context**, since backgrounded input is often already at end-of-file — bounding the process's lifetime
  externally avoids depending on that option at all.
- **A connection to a port with no matching forward fails silently** in exactly the same way a real
  misconfiguration would, which can look identical to a design problem until the missing rule is spotted.
- Not every kernel used in development or testing has every optional feature available (a specific traffic-shaping
  feature, in one case); a test that depends on one should fail loudly if it's missing, not silently do nothing.
