# Trust register

ccgram runs with a Telegram bot token, types into the person's terminal sessions, writes hooks into
Claude Code's settings and sends requests to model providers. Code that runs here can do all of that,
so this repository keeps a register (the seats kit's base, "Code from elsewhere, where a repo holds
private things").

**The rule:** before code from outside this repository's own history runs here in any way (tests, an
install, a build, a hook, a launch), check its source's standing below.

- **trusted**: it runs.
- **audit**: the whole incoming change is read first. Every file outside what it claims to do is
  named, and so is every place that runs by itself: build config, test fixtures, git hooks, CI, agent
  settings and instructions. The person approves before anything executes, unless the source's row
  below records that they approved a seat's gate as that reading.
- **untrusted**: read, never run.

A source that is not listed is untrusted. Only the person sets a standing; a session never raises
one itself.

## Sources

| Source | Standing | Why | Decided |
|---|---|---|---|
| `upstream`: its commits and its tags | **audit** | Third-party code that holds the bot token and types into the person's sessions. Each release is read by the downstream seat's gate (`docs/seats/DOWNSTREAM.md`) before a test or an install runs it; the record is `docs/FORK.md`. Proposed in the adoption pull request: the gate approved as the audit, its read-in-full pass done by an Opus subagent the seat briefs with the gate's list, the verdict the seat's. Until the person's merge sets it, a release's tests wait for the person. | the standing: the person, 2026-10-17; the gate as the audit: the person's merge of the adoption pull request |
| The seats kit, at the tag this clone pins | **trusted** (proposed in the adoption pull request) | The person's own repository, pinned by tag. Its hooks and watchdog run in every session here. Each new release adopted on its announcement. | the person's merge of the adoption pull request |

## Outside the rule

- **This repository's own history**, `origin`, including what it merged from `upstream` after the gate.
- **Packages pinned in `uv.lock` from the package index**: the pin is their standing. A new or changed
  dependency is still read by the gate.
- **The person's user-level Claude Code setup**: it loads in sessions here, but this repository does
  not pull it in.

## Not listed, so untrusted

Pull requests and branches from other forks, before `upstream` merges them. Code pasted in from an
issue or a chat. Any file another session leaves in the working tree uncommitted.
