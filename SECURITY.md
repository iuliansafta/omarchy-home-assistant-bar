# Security and publication gates

This project is not security-certified. Report vulnerabilities privately to [iulian.safta@gmail.com](mailto:iulian.safta@gmail.com). Do not put credentials, private host addresses, raw config/logs or exploit details involving a live installation in public issues; use synthetic reproductions where possible.

## Boundaries

- Refresh tokens belong in Secret Service; access tokens remain in controller memory except intentional `HASS_TOKEN` injection into a `hass-cli` child environment. No credentials belong in QML, IPC, config, logs or argv. Same-UID processes and user-controlled executable configuration are trusted; child environments are not isolation from that user.
- The IPC directory/socket use 0700/0600 permissions and Linux peer UID checks fail closed. This is not a multi-user server.
- HTTPS is strongly recommended. HTTP remains supported and exposes credentials to network observers/interceptors, regardless of application redirect checks.
- Token/revocation POSTs do not follow redirects. WebSocket authentication rejects a final origin change (including TLS downgrade) before sending credentials; same-origin redirects remain allowed. This relies on aiohttp's internal final-response URL and must be retested when upgrading aiohttp.
- Session-generation guards discard delayed token successes/errors after cancellation, server replacement or signout. Local signout precedes best-effort remote revocation; a failed revoke requires manual revocation in the HA profile. Keyring availability/deletion must be checked independently if the keyring is locked or failing.
- CLI validation and literal-secret output scrubbing are defense in depth, not a sandbox or a guarantee against arbitrary secret encodings. Output is bounded per subprocess, not globally across concurrent same-user clients.

## Release gates

1. MIT was selected by the owner; see [LICENSE](LICENSE). Confirm third-party attribution obligations before tagging a release.
2. The public `main` branch was created as a clean root commit from sanitized current files; prior local development history was not published. Review future refs, artifacts and metadata before pushing them.
3. The pre-publication reachable-blob scan found no JWT/private-key signatures, but was not a dedicated or exhaustive secret scan. Rotate credentials if actual exposure is discovered.
4. Independently review fixes and run all offline tests without skips. Obtain approval before installation, desktop restart, keyring operations or real device testing. Record exact host compatibility and release acceptance before tagging a plugin release.

## Publication status

Version `0.1.0` is prepared for release; it has not been tagged or published. Release notes are in [docs/releases/v0.1.0.md](docs/releases/v0.1.0.md).

Acceptance recorded on 2026-09-13:

- All 197 offline tests passed without skips, including 14 new session-recovery regressions. Independent review of the controller fixes found no issues.
- With owner approval, signed out, verified local keyring credential removal, removed the development symlink, and installed a fresh GitHub clone with the reviewed local changes overlaid. Both runtime environments were provisioned afresh; the controller ran from the installed checkout and the shell restarted successfully.
- The owner confirmed Home Assistant login, individual lights, area on/off, brightness and colors working on the reinstalled plugin.
- This validates the local release candidate, not an already-published GitHub version. Authenticated restart/token refresh, network reconnect, offline non-replay, keyboard navigation, multi-monitor routing and white temperature have not been separately confirmed live. Attribution and final artifact/privacy checks remain release gates.

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
