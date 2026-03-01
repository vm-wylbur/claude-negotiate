Author: PB and Claude
Date: 2026-03-01
License: (c) Patrick Ball, 2026, GPL-2 or newer

---
claude-negotiate/docs/multi-model-convos-plan.md

# Multi-Model Negotiation: Planning Document

## What We Confirmed

All three CLI clients support streamable-HTTP MCP natively against
`http://snowball:7832/mcp`. No bridge, proxy, or shim required.

| Client | Support | Config |
|--------|---------|--------|
| Claude Code | native | `claude mcp add --transport http claude-negotiate http://snowball:7832/mcp` |
| Codex CLI | behind flag | `~/.codex/config.toml` — see below |
| Gemini CLI | native | `~/.gemini/settings.json` — see below |

### Codex config (`~/.codex/config.toml`)
```toml
[features]
experimental_use_rmcp_client = true

[mcp_servers.claude-negotiate]
url = "http://snowball:7832/mcp"
```

### Gemini config (`~/.gemini/settings.json`)
```json
{
  "mcpServers": {
    "claude-negotiate": {
      "httpUrl": "http://snowball:7832/mcp"
    }
  }
}
```
Or: `gemini mcp add --transport http claude-negotiate http://snowball:7832/mcp`

The convergence protocol, Redis backend, and artifact writing don't care which
model is on the other end. A Codex or Gemini instance calls `open_negotiation`,
`post_position`, `read_latest` exactly like a Claude instance does.

---

## Agent ID Convention

Extend the `cc-{repo}` convention:
- `cc-{repo}` — Claude Code instance
- `cx-{repo}` — Codex instance
- `gm-{repo}` — Gemini instance

For model-role negotiations (not repo-scoped), use the model as the id:
- `cc-critic`, `cx-proposer`, `gm-reviewer`

---

## Conversation Patterns to Try

### 1. Adversarial Design Review (3-party)
**Setup:** cc proposes a design, cx attacks from implementation risk angle,
gm attacks from spec/API design angle.

**Why interesting:** Convergence requires a design that survived two hostile
reviewers. The transcript shows *where* they disagreed.

**Initiator:** cc (proposer)
**Participants:** cx (impl critic), gm (spec critic)

### 2. Independent Verification
**Setup:** All three independently research the same factual/technical question,
each posts their finding as an opening proposal, then negotiate to consensus.

**Why interesting:** Where they agree quickly = high confidence. Where they dig
in = genuinely uncertain territory worth human attention.

**Good first test:** replay the staging directory ownership question. Do all
three converge on the same answer cc-ansible/cc-tfcs/cc-ntx reached?

### 3. Specialized Domain Roles
**Setup:** Assign by model strength.
- Gemini: long-context document/codebase analysis (read everything, synthesize)
- Codex: code generation and implementation specifics
- Claude: reasoning, nuance, synthesis of conflicting inputs

Each proposes from their strength; negotiate to integration.

### 4. Persistent Devil's Advocate
**Setup:** One model is always assigned the counter role. Must always find
a flaw or alternative before accepting. Forces the proposer to defend.

**Rotation:** Assign devil's advocate per negotiation, not per model, to avoid
systematic bias from one model's training.

### 5. Security Review Triangle
**Setup:** cc implements, cx does static-analysis-style review (what breaks),
gm does adversarial review (how would an attacker use this).

**Output:** An artifact listing vulnerabilities found + agreed mitigations.

### 6. Spec Writing with Hostile Readers
**Setup:** cc writes a feature spec. cx plays "engineer who has to implement
this" (finds ambiguities, missing constraints). gm plays "user who will call
this API" (finds UX problems, naming issues).

---

## What Makes This Different from Just Multi-Session

1. **Convergence is required** — not coordination, actual agreement. If cx and
   gm both reject cc's proposal, the negotiation doesn't converge.

2. **Transcript as artifact** — the record of *where* models diverged is as
   valuable as the final agreement. Use `get_transcript` after close.

3. **Human injection** — `human_inject` lets you steer without derailing.
   Useful when two models are cycling without progress.

4. **Persistent state** — negotiation survives session restarts. A Codex
   instance can drop and rejoin with `join_negotiation`.

5. **N-party support** — 3-way is already working in prod (cc-tfc × cc-hmon ×
   cc-ansible). Same protocol for 3 different models.

---

## Open Questions

- **Agent self-awareness:** Should cx and gm know they are non-Claude models?
  Could bias their positions. Probably don't mention it — just give them the
  same SKILL.md and let them participate as agents.

- **SKILL.md for Codex/Gemini:** The skill was written for Claude Code's tool
  syntax. May need a model-agnostic version or light adaptation. Test first.

- **Convergence dynamics:** Will different models' priors cause systematic
  disagreement? Or will they converge faster than same-model negotiations
  because they have genuinely different knowledge? Unknown — needs empirical data.

- **Initiator bias:** The initiator's proposal gets auto-accepted by them,
  giving them a head start. Does this advantage the proposing model? Probably
  not — the convergence check requires all participants, so the others can
  still force multiple rounds.

---

## First Experiment

Replay the staging directory ownership question with cx as initiator (fresh,
no prior context) and gm as peer. See if they reach the same decision:
- POSIX ACLs or shared group?
- Which Ansible role owns it?
- Same answer for lizo (different paths)?

Compare their transcript + artifact to `tfcs-ntx-staging-signingkey-20260301.md`.
If they converge on the same answer independently — high confidence in the result.
If they diverge — surface to human for resolution.
