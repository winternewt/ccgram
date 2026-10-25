# Run the mobile API

The API runs inside the existing ccgram process. Telegram configuration is still
required. An unset `CCGRAM_API_PORT` disables the HTTP listener. The shared session
core still runs for Telegram.

## Start locally

Add these values to the ccgram environment or its `.env` file:

```dotenv
CCGRAM_API_PORT=8766
CCGRAM_API_HOSTS=127.0.0.1:8766,localhost:8766,mac.example-tailnet.ts.net
```

Replace `mac.example-tailnet.ts.net` with the HTTPS hostname of your Mac.
Start or restart ccgram normally:

```console
ccgram run
```

The listener always binds `127.0.0.1`. There is no configurable external bind
address. The Mini App keeps its separate listener and authentication.

| Setting | Meaning |
| --- | --- |
| `CCGRAM_API_PORT` | Enable the API on this port, from 1 through 65535. |
| `CCGRAM_API_HOSTS` | Comma-separated exact HTTP Host authorities. Defaults to `127.0.0.1:<port>,localhost:<port>`. Include a port when the client sends one. |
| `CCGRAM_API_ORIGINS` | Comma-separated exact browser Origins. Empty by default. Native clients send no Origin. There is no cookie authentication or CORS grant. |

## Expose it through Tailscale

Configure Tailscale on the Mac and phone, then run:

```console
tailscale serve --bg http://127.0.0.1:8766
```

Use the resulting HTTPS origin for pairing. Keep the tailnet access policy limited
to your devices. ccgram checks the forwarded Host against `CCGRAM_API_HOSTS`.
This guide does not create or change a tunnel automatically.

Cloudflare Tunnel requires Cloudflare Access in front of the API. The client must
provide the Access headers as well as the ccgram bearer token. A public tunnel
without Access leaves the device token as the only authentication layer.

## Pair and revoke

Run on the Mac:

```console
ccgram pair --url https://mac.example-tailnet.ts.net
```

The command prints a terminal QR code and its JSON payload. `--json` prints only
JSON. `--config-dir` selects the same ccgram directory as the running server.
The payload contains the HTTPS origin, protocol, one-time code, and server identity
fingerprint. It expires after 120 seconds. Pairing atomically consumes the code.

The fingerprint identifies this ccgram state directory. It is not a TLS certificate
pin. Verify the HTTPS hostname and certificate through the operating system. Compare
the fingerprint with the Mac before accepting a host. The phone stores the returned
device token in Keychain.

```console
ccgram devices list
ccgram devices revoke d_EXAMPLE
```

All paired devices have the operator role and can see and control all eligible
sessions. Revocation rejects new requests immediately and closes an idle WebSocket
within about one second. A request already executing can finish.

## Stored state and recovery

`protocol.sqlite3` in the ccgram directory contains session bindings, transcript
generations, device-token hashes, pairing-code hashes, action outcomes, and delivery
correlations. It stores no transcript database or submitted action bodies. Rejected action
outcomes can include the returned error snapshot and its request details. Treat
the database as sensitive. Upload bytes live
in the adjacent private `uploads/` directory. Uploaded files remain until the operator
removes them. There is no automatic retention policy.

SQLite uses full synchronous commits. Before input starts, the server commits an
`unknown` outcome for the action ID. A crash leaves that outcome in place. Repeating
that ID returns the recorded outcome and sends no input. Keep the database when
restarting or restoring ccgram. Removing or restoring an older database discards
newer deduplication records and can make old action IDs executable again.

The database and uploads use mode `0600`. Newly created private directories use
`0700`. Back up the database and uploads together when ccgram is stopped.
Action outcomes have no automatic expiry, so the deduplication guarantee has no
short retry window.

One ccgram session core per multiplexer backend is allowed for each OS user. A process
lock under `~/.cache/ccgram/locks/` enforces this even across different ccgram
configuration directories and tmux session names. Separate tmux sessions can
share linked windows, so a lock for each session cannot prevent overlapping
writers. For work and personal groups, use the
[single-core group setup](../guides.md#multi-instance-setup).

## Status refresh and native probes

Native agent status is optional enrichment. Telegram status polling and the
shared core use the same cache during bulk refresh. The core refreshes targets
once per second, stores its empty probe results for five seconds, and keeps
positive probes live. A backend's event-stream capability does not guarantee
pushes for every core target: Herdr watches Telegram-bound targets only.
Telegram polling uses pushed status when available; backends without a push
stream cache empty results for five seconds and leave positive results live.

The Herdr and Agterm backends return `None` for expected transport or response
errors. On a backend without a push stream, this value uses the five-second
negative cache. Native `unknown` is a separate value. It is not negative or
inactive. Raised probe errors are not cached. ccgram logs a rate-limited warning
and retries on the next refresh. A failed Telegram probe does not stop other
window ticks. A failed shared-core probe does not discard other targets or stop
the runtime poll. This hardening covers unexpected raised errors. It does not
indicate a reproduced socket failure.

This reduces repeated native-status probes when no status is available. It does
not reduce the cost of a complete refresh. Each refresh still enumerates targets,
reads foreground and panes, checks prompt/request state, and observes transcripts.
A positive status from a backend without a push stream is not cached. Another
reader can probe again on its next refresh. The five-second cache is only for
optional background status enrichment. Direct `inspect()` calls used by action
revalidation and hook/request validation bypass this cache. Identity lookup,
prompt/request detection, and terminal-input guards continue to read fresh state.
If a warning persists, check multiplexer health and the error details in the
ccgram log.

## Remote input and approval modes

The API has no operation for raising the approval mode. Remote session creation
accepts only `normal`. Resuming a previous conversation also uses the configured
normal launch command. Local command overrides remain operator-controlled.

Raw terminal keys and paste are privileged terminal input. They can run shell
commands or navigate an agent's permission settings. The no-YOLO API restriction
cannot constrain what a device does through those terminal controls. Pair only
trusted operator devices. A shell accepts raw terminal input. `message.send` is
unavailable there, so an agent message cannot accidentally become a shell command.

An existing terminal target can still be closed when its transcript is unreadable
or its foreground is unknown. The session advertises `session.close` in this
state. Closing requires a confirmed target and the current binding version.
An unavailable backend or stale binding rejects the action. Input remains blocked
until the source and foreground can be verified.

APNs registration returns `422 unsupported_action`. `push.apns` is absent from
capabilities. Telegram remains the notification channel. This implementation does
not install or publish an iOS application.

## Hook processing recovery

Hook intake blocks approvals and messages until queued evidence is processed.
If the queue overflows, the log reports `Protocol hook queue full`. If processing
fails, it reports `Session protocol source unavailable`. Lost observations keep
affected sessions blocked because the server cannot prove which request is current.
Terminal controls remain available.

Fix the hook flood or unavailable source, then restart ccgram to rebuild state
from fresh observations. Refresh the mobile session and confirm the displayed
request before sending a new action. Do not retry an action with an unknown outcome.
