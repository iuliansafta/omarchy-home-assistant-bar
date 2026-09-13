# Security and publication gates

This project is not security-certified. Report vulnerabilities privately to [iulian.safta@gmail.com](mailto:iulian.safta@gmail.com). Do not put credentials, private host addresses, raw config/logs or exploit details involving a live installation in public issues; use synthetic reproductions where possible.

## Boundaries

- Refresh tokens belong in Secret Service; access tokens remain in controller memory except intentional `HASS_TOKEN` injection into a `hass-cli` child environment. No credentials belong in QML, IPC, config, logs or argv. Same-UID processes and user-controlled executable configuration are trusted; child environments are not isolation from that user.
- The IPC directory/socket use 0700/0600 permissions and Linux peer UID checks fail closed. This is not a multi-user server.
- HTTPS is strongly recommended. HTTP remains supported and exposes credentials to network observers/interceptors, regardless of application redirect checks.
- Token/revocation POSTs do not follow redirects. WebSocket authentication rejects a final origin change (including TLS downgrade) before sending credentials; same-origin redirects remain allowed. This relies on aiohttp's internal final-response URL and must be retested when upgrading aiohttp.
- Session-generation guards discard delayed token successes/errors after cancellation, server replacement or signout. Local signout precedes best-effort remote revocation; a failed revoke requires manual revocation in the HA profile. Keyring availability/deletion must be checked independently if the keyring is locked or failing.
- CLI validation and literal-secret output scrubbing are defense in depth, not a sandbox or a guarantee against arbitrary secret encodings. Output is bounded per subprocess, not globally across concurrent same-user clients.

## Sharing diagnostics

Review logs, configuration and command history before sharing. Remove private URLs, identities and credentials.

## Credential cleanup

Use widget **Sign out** while the controller and keyring are available. If remote revocation fails, revoke the session in your Home Assistant profile too. Verify deletion of the Secret Service entry (service `omarchy-home-assistant`, account `ha-refresh-token`) with your keyring manager. Deleting configuration does not delete keyring credentials.

## Release acceptance checklist

Before tagging a release:

- Complete independent security review and all offline checks without skips.
- Review attribution, reachable Git history, screenshots and release artifacts for publication; see [release gates](#release-gates).
- Record exact host versions and test a fresh install, browser sign-in, restart/refresh, signout, reconnect and upgrade/removal.
- Verify QML loading, keyboard controls and multi-monitor panel routing live.
- With permission, verify individual/area actions, brightness, color and white temperature against observed HA state; confirm offline actions are never replayed.
- Agree release notes and version/tag. No automatic publishing workflow is provided.
