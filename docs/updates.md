# Updates

GitHub is the single source of software for this project. New code lands on the `main` branch, but nothing
gets deployed until a version is explicitly released — pushing an ordinary commit never changes anything
running anywhere. A release is cut deliberately, and every deployment decides for itself, on its own schedule,
whether to move onto one.

## How a release is cut

Pushing a version tag (`v1.2.3`, following `vMAJOR.MINOR.PATCH`) to GitHub triggers a workflow that:

1. builds the web UI,
2. packages the backend, the built UI, and the installed-system's own updater into one tarball,
3. publishes it as a GitHub release, with that tarball attached.

```bash
git tag v1.2.3
git push origin v1.2.3
```

Nothing needs to run locally beyond that — the build happens on GitHub, not on this machine or the Pi.

## Updating a Raspberry Pi install

```
pmctl update
```

This is the whole workflow in one command, modeled on `apt update && apt upgrade`:

1. **Check** — asks GitHub for the latest release and compares it against the version recorded at install time
   (`/opt/portmirror/VERSION`). If already current, it says so and stops here — nothing is downloaded or
   changed.
2. **Show** — if a newer release exists, prints its version and release notes.
3. **Ask** — waits for a `y`/`N` confirmation before touching anything running.
4. **Apply** — downloads the release tarball, stops the services, replaces the application files, the `pmctl`
   command itself, and the updater script, reinstalls Python dependencies if they changed, and restarts the
   services. `pmctl` stays current through this same flow, not just the backend and UI.

`pmctl update check` only does step 1 — useful for checking without being asked anything (exit code `2` means
an update is available, `0` means already current). `pmctl update --yes` skips the confirmation prompt, for a
scripted or unattended update.

**If the new version fails to come up** (the services don't start, or the manager doesn't stay running), the
update rolls itself back automatically to the previous install and reports that the update was not applied —
the gateway is never left on a broken half-updated state.

A Docker deployment updates the ordinary way instead: `git pull`, then `docker compose up -d --build`.

## Version numbering

Releases follow `vMAJOR.MINOR.PATCH`. There's no enforced policy yet on what bumps which number — for a
single-maintainer project, cutting a release is itself the meaningful event; the number mostly exists so
`pmctl update` has something to compare.
