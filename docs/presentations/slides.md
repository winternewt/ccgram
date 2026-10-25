---
theme: default
title: Remote AI Development Through Terminal Continuity
author: Alexei Ledenev
presenter: true
colorSchema: light
transition: slide-left
clickAnimation: fade-in
aspectRatio: 16/9
canvasWidth: 980
fonts:
  sans: Inter
  mono: JetBrains Mono
layout: cover
class: ccg-cover
---

<!-- markdownlint-disable MD025 MD003 MD022 -->

# Remote AI development<br><span class="accent">through terminal continuity</span>

<div class="subtitle">My workflow: tmux → Telegram → tmux again</div>

<div class="hero-flow">
  <div class="hero-node">🖥️ tmux</div>
  <div class="arrow">→</div>
  <div class="hero-node hot">📱 Telegram</div>
  <div class="arrow">→</div>
  <div class="hero-node">💻 laptop / SSH / Tailscale</div>
</div>

<!--
Opening frame. This talk is not mainly about a tool. It is about preserving continuity while working with AI agents away from the desk.

The implementation I use today is CCGram, but the approach is bigger: keep the working agent in tmux, expose a phone-friendly control plane, then reattach from laptop or SSH/Tailscale later.

Timing: 45 seconds.
-->

---

<div class="slide-shell ccg-bigpoint">

<div class="bigline">The agent keeps working.<br><span class="muted">My attention moves.</span></div>

</div>

<!--
Problem slide.

AI coding agents run for minutes or hours. They ask for approvals, hit tests, need clarification, or finish while I am away. The problem is not only remote access. It is preserving the same working context across places.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-principle">

<div class="kicker">Principle</div>

# Do not move the agent. Move the controls

<div class="principle">
  <div v-click.up class="card"><div class="icon">🧠</div>conversation stays live</div>
  <div v-click.up class="card"><div class="icon">📂</div>files stay local</div>
  <div v-click.up class="card"><div class="icon">🔁</div>reattach anytime</div>
</div>

</div>

<!--
This is the core approach.

I do not want a second chat copy in a mobile app. I do not want to migrate the session into a cloud sandbox every time I stand up. I want the terminal session to remain authoritative, and the phone to be a control surface.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-story">

<div class="kicker">Origin story</div>

# It started with ccbot

<div class="timeline">
  <div v-click.up class="timeline-step"><b>ccbot</b><span>useful Telegram bridge for Claude Code</span></div>
  <div v-click.up class="arrow">→</div>
  <div v-click.up class="timeline-step"><b>PRs</b><span>some needs did not land fast enough</span></div>
  <div v-click.up class="arrow">→</div>
  <div v-click.up class="timeline-step"><b>fork</b><span>optimize for my daily workflow</span></div>
  <div v-click.up class="arrow">→</div>
  <div v-click.up class="timeline-step"><b>CCGram</b><span>implementation of the approach</span></div>
</div>

</div>

<!--
Tell this personally, not as marketing.

I started from the open-source ccbot project. It solved the first real problem: control Claude Code from Telegram. But it was Claude-only and lacked workflow features I needed. I tried contributing, but PRs moved slowly, so I forked and pushed it in the direction I actually use every day.

Important wording: CCGram is not the topic. It is the artifact that came out of the workflow pressure.

Timing: 1.5 minutes.
-->

---

<div class="slide-shell ccg-loop">

# The loop I care about

<div class="loop">
  <div class="loop-node"><b>Start</b><span>tmux on laptop or devbox</span></div>
  <div class="arrow">→</div>
  <div class="loop-node"><b>Steer</b><span>Telegram while away</span></div>
  <div class="arrow">→</div>
  <div class="loop-node"><b>Return</b><span>tmux attach over laptop, SSH, or Tailscale</span></div>
</div>

<div class="loop-caption">Continuity is the product.</div>

</div>

<!--
This is the mental model.

I may start locally, leave the desk, answer approvals from Telegram, then return from the same laptop or another machine over SSH/Tailscale. The important thing is that I do not need to reconstruct context. It is still the same tmux window and same agent process.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-map">

# One topic. One window. One working context

<div class="topic-map mt-12">
  <div class="panel">
    <div class="panel-title">Telegram group</div>
    <div class="topic">🟢 api-refactor</div>
    <div class="topic">🟡 ui-tests</div>
    <div class="topic">📡 ops-shell</div>
  </div>
  <div class="arrow">⇄</div>
  <div class="panel">
    <div class="panel-title">tmux session</div>
    <div class="window">@3 · claude · /repo/api</div>
    <div class="window">@4 · codex · /repo/ui</div>
    <div class="window">@5 · bash · /infra</div>
  </div>
</div>

</div>

<!--
The concrete implementation pattern.

A Telegram Group topic maps to one tmux window. The stable identity is the tmux window id. The topic is just a mobile handle for the live terminal context.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-demo">

<div class="kicker">Live demo</div>

# Create a remote work session from the phone

<v-clicks class="demo-list">

1. **New topic**
2. **Pick repo**
3. **New worktree**
4. **Choose agent**
5. **Reattach tmux**

</v-clicks>

<div class="demo-hint">Mirror iPhone → Telegram → show tmux window appearing on desktop</div>

</div>

<!--
Short demo script. Keep it under 3 minutes.

Before presentation:
- Mirror iPhone to desktop.
- Have Telegram group with topics enabled.
- Have CCGram already running in tmux control window.
- Have a small repo ready, ideally a throwaway branch/worktree.
- Keep a terminal visible with `tmux attach -t ccgram`.

Demo steps:
1. In Telegram, create topic `demo-remote`.
2. Send a small prompt: `check the README and suggest one small improvement`.
3. Use directory browser to choose the repository.
4. Choose new worktree and accept the suggested branch name.
5. Choose an agent already authenticated locally.
6. Show the tmux window appears on desktop.
7. Send one follow-up from Telegram.
8. Tap Screenshot or Live.
9. Reattach in tmux to show it is the same session.

Line to say: I am not starting a cloud job. I am creating a local tmux window and controlling it from Telegram. The same session is still available through tmux.

Timing: 3 minutes.
-->

---

<div class="slide-shell ccg-bigpoint">

<div class="bigline">The terminal remains the <span class="accent">source of truth</span></div>

<div class="pills">
  <div v-click.up class="pill">📱 phone can steer</div>
  <div v-click.up class="pill">🖥️ desktop can reattach</div>
  <div v-click.up class="pill">🔐 host keeps files and credentials</div>
</div>

</div>

<!--
Post-demo interpretation.

No session transfer. No separate chat copy. No hidden cloud clone. The tmux process is the thing. Telegram is just the control plane.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-features">

# Features came from workflow pressure

<div class="feature-grid">
  <div v-click.up class="feature"><b>More agents</b><span>Claude, Codex, Gemini, Pi, Shell</span></div>
  <div v-click.up class="feature"><b>Voice mode</b><span>phone-native prompts with send/discard</span></div>
  <div v-click.up class="feature"><b>Shell LLM</b><span>natural language → command → approval</span></div>
  <div v-click.up class="feature"><b>Worktree topics</b><span>new task, isolated branch</span></div>
  <div v-click.up class="feature"><b>Live inspection</b><span>screenshots, live view, transcript</span></div>
  <div v-click.up class="feature"><b>Artifact fetch</b><span>/send files back to Telegram</span></div>
</div>

</div>

<!--
This is where CCGram appears as proof, not the main subject.

I added features when the workflow demanded them. Claude-only was not enough because I use Codex, Gemini, Pi, and sometimes a shell. Phone input made voice useful. Shell mode needed an LLM to turn intent into a command, but still with approval. Worktree topics matter because remote tasks should not trash my current branch. Screenshots, live view, transcript, and file sending are the minimum inspection loop.

Timing: 1.5 minutes.
-->

---

<div class="slide-shell ccg-providers">

# 🤖 No single agent wins every task

<div class="provider-row">
  <div class="provider">Claude</div>
  <div class="provider">Codex</div>
  <div class="provider">Gemini</div>
  <div class="provider">Pi</div>
  <div class="provider">Shell</div>
</div>

<div class="contract mt-10">The workflow should switch agents without switching remote-control model.</div>

</div>

<!--
Why multi-provider matters.

This is not a vendor argument. It is practical. Different agents and CLIs are better for different tasks, or are available under different constraints. The remote workflow should not collapse just because I use Codex today and Pi tomorrow.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-architecture">

# The implementation stays thin

<div class="arch-flow">
  <div v-click.up class="arch-node"><span>📱</span><b>Telegram</b><small>phone UI</small></div>
  <div v-click.up class="arch-arrow">→</div>
  <div v-click.up class="arch-node"><span>🧭</span><b>Control layer</b><small>routing + formatting</small></div>
  <div v-click.up class="arch-arrow">→</div>
  <div v-click.up class="arch-node"><span>🖥️</span><b>tmux</b><small>send keys + capture</small></div>
  <div v-click.up class="arch-arrow">→</div>
  <div v-click.up class="arch-node"><span>🤖</span><b>Agent CLI</b><small>Claude · Codex · Gemini · Pi · Shell</small></div>
</div>

<div v-click.up class="arch-return">signals return: transcripts · hooks · terminal output</div>

<div class="diagram-caption mt-6">The control layer is replaceable. The continuity pattern is the point.</div>

</div>

<!--
Architecture in one diagram.

The tool I use is CCGram, but the architecture is intentionally boring: Telegram UI, routing/formatting layer, tmux I/O, agent CLI, transcripts/hooks/terminal output back. The control layer is useful because it is thin and does not own the agent brain.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-alternatives">

# Built-in remote tools are still useful

<div class="alt-grid">
  <div v-click.up class="alt">Claude<br><span>Remote Control</span></div>
  <div v-click.up class="alt">Codex<br><span>Remote Connections</span></div>
  <div v-click.up class="alt">Claude<br><span>Dispatch</span></div>
</div>

<div v-click.up class="diagram-caption mt-10">I use the terminal-continuity approach when I need tmux + Telegram + mixed agents.</div>

</div>

<!--
Do not attack alternatives.

Claude Remote Control is great for Claude's native remote UI. Codex Remote Connections are great in Codex App and ChatGPT mobile. Dispatch is great for asking Desktop to start new work from the phone.

My approach is different: preserve the live terminal session and make it reachable from Telegram across agents.

Timing: 1.5 minutes.
-->

---

<div class="slide-shell ccg-matrix">

# Choose by the constraint

| Constraint                  | Built-in remote | Terminal continuity |
| --------------------------- | --------------: | ------------------: |
| Best native vendor UI       |         **yes** |                  no |
| Same tmux session           |       sometimes |             **yes** |
| Multi-agent workflow        |              no |             **yes** |
| Reattach over SSH/Tailscale |   not the model |             **yes** |

</div>

<!--
Decision slide.

If I want the vendor's native app UX for one vendor, the built-in remote feature wins. If I want continuity through tmux, with different agents, and reattach from another machine, terminal continuity wins.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-quadrant">

# Four remote-development modes

<div class="quadrant">
  <div class="axis x">same live session →</div>
  <div class="axis y">agent-neutral ↑</div>
  <div class="q q1"><b>tmux + Telegram</b><span>continuity cockpit</span></div>
  <div class="q q2"><b>Claude RC</b><span>native Claude control</span></div>
  <div class="q q3"><b>SSH + tmux</b><span>raw terminal</span></div>
  <div class="q q4"><b>Cloud / Dispatch</b><span>spawn async work</span></div>
</div>

</div>

<!--
Clarify categories.

Cloud and Dispatch are great when same-session continuity does not matter. SSH is same-session, but it is a raw terminal experience on a phone. Native remote control is great but vendor-specific. The quadrant I use most is same-session plus agent-neutral.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-security">

# Guardrails matter

<div class="security-lanes">
  <div class="lane"><b>Phone</b><span>Telegram identity and bot access</span></div>
  <div class="lane"><b>Control layer</b><span>routing, state, formatting</span></div>
  <div class="lane"><b>tmux host</b><span>repos, tools, credentials</span></div>
</div>

<div class="guardrail mt-10">Treat Telegram access as operational access.</div>

</div>

<!--
Be explicit about risk.

This approach makes your terminal remotely operable. That is powerful. The host still owns files, credentials, MCP servers, browser sessions, and shell tools. Telegram controls must be restricted: allowed users, group restriction, careful bot permissions, and no casual public exposure of web surfaces.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-limits">

# Limits of the approach

<v-clicks class="limit-list">

- 🛌 Host asleep → no work
- 📶 No network → no control
- 🧩 Terminal UIs change → parsing can break
- 🔐 Phone control expands the blast radius

</v-clicks>

</div>

<!--
Do not oversell.

This is a local/devbox control-plane pattern. It is not a cloud runner. If the machine must keep working while offline, use a cloud agent or vendor cloud session. Terminal parsing is less stable than official APIs, although hooks and JSONL transcripts reduce the risk.

Timing: 1 minute.
-->

---

<div class="slide-shell ccg-takeaway">

<div class="takeaway">The tool is incidental.<br><span class="accent">Continuity is the architecture.</span></div>

<div class="final-flow">
  <span>Start in tmux</span>
  <span class="arrow">→</span>
  <span>steer from Telegram</span>
  <span class="arrow">→</span>
  <span>reattach anywhere</span>
</div>

<div class="qa">Questions</div>

</div>

<!--
Final recap.

The takeaway is not "use my tool." The takeaway is: if your AI agents do serious work in terminals, make the terminal session durable and make the controls mobile. CCGram is my current implementation because I needed multi-agent support, voice, shell command generation, worktrees, screenshots, live inspection, file fetch, and reattachability.

Timing: 45 seconds plus Q&A.
-->
