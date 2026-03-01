Author: PB and Claude
Date: 2026-03-01
License: (c) Patrick Ball, 2026, GPL-2 or newer

---
claude-negotiate/docs/c-c-feedback.md

# Agent Feedback Log

Running record of what the negotiating agents said after each live test,
plus synthesis and disposition.

---

## Iteration 1: First test (neg-74ef1173)

**Topic:** Basic validation — cc-a (scott/hrdag-monitor) vs cc-b (local/TFC), simple coordination topic.

**Result:** 5 turns, converged. Artifact written to /tmp/negotiate-test.md.

**Feedback:** No structured post-mortem. Validated that the protocol worked end-to-end.

**Changes made:** None from this run. Prompted design of `wait_for_turn`.

---

## Iteration 2: Storage assessment

**Topic:** Do the TFC-managed servers have sufficient storage?

**Result:** Converged. Both agents gave structured post-mortem feedback.

### Agent feedback

**Bugs:**
1. Convergence race in `wait_for_turn` — returned `converged=False` even after convergence was declared
2. `wait_for_turn` returned self-turns — agent saw its own just-posted turn before peer responded
3. `accepting_hash` not visible in `read_latest` turns — peer couldn't see which hash was accepted

**Protocol gaps:**
4. Two-phase accept was awkward — proposer had to post a second `accepting` after peer accepted
5. `close_negotiation` required passing `final_artifact` explicitly
6. No round count in responses — agents couldn't pace themselves
7. No way to read artifact from a different host than the server
8. No single-call join for peers — had to manually call `read_latest` with `since_id="0"`
9. SKILL.md didn't explain when to use `read_latest` vs `wait_for_turn`

### Synthesis

All nine items were legitimate. Items 1-3 were bugs with clear fixes. Items 4-9 were missing protocol
features, all straightforward to add.

### Disposition

Fixed in four commits (edeaff5, 0c76e23, 4bd0079, 7a6e8af):
- Bug fixes: convergence race (retry loop), self-turns filter, accepting_hash in turn dicts
- Single-accept convergence (proposer auto-accepts own proposal)
- `close_negotiation` auto-fills artifact from stream if `final_artifact` omitted
- Round count (`turns_used`, `max_turns`) in all response paths
- `get_artifact` tool for remote artifact access
- `join_negotiation` tool for one-call peer entry
- SKILL.md: `read_latest` vs `wait_for_turn` heuristic, round budget guidance

---

## Iteration 3: Metrics negotiation (second round)

**Topic:** What node_exporter metrics should hmon collect and alert on for TFC-managed hosts, and at what thresholds?

**Result:** Converged in 5 of 20 turns. Artifact at `/tmp/hmon-tfc-metrics-agreement.md`. The negotiation surfaced
genuine cross-repo knowledge: snowball missing from hmon fleet, `/var/tmp` gap, `ntx-scan.service` name,
per-org ZFS dataset structure.

### Agent feedback (cc-hmon)

1. **Artifact was raw turn, not clean artifact.** The converged content included the preamble ("Excellent
   analysis. I accept your threshold adjustments...") and Q&A before the actual agreement table. The intended
   artifact was just the `## Proposed combined agreement` section.
2. **`join_negotiation` should be the documented primary peer entry point** — it returned role, transcript,
   and `last_id` in one call, much smoother than manual `read_latest`.
3. **No way to attach files as evidence.** Had to paste grep findings inline. Wants
   `attach_evidence(neg_id, agent_id, file_path, description)`.
4. **Single-accept convergence confusion.** Thought it was missing; actually worked correctly. Flagged as
   inconsistency but likely a misreading of the previous run.
5. **`wait_for_turn` doesn't show turn budget while blocking.** Round count only appears after the peer
   responds, not during the wait.

### Agent feedback (cc-tfc)

1. **`wait_for_turn` still returns self-turns.** After posting turn 4, wait returned its own turn before
   timing out. This was supposed to be fixed in iteration 2.
2. **Convergence detection inconsistent across runs.** First run required `get_status` to discover convergence;
   second run `wait_for_turn` returned `converged=True` correctly. Likely a timing race, not fixed by the
   iteration 2 patch.
3. **Artifact quality is a coordination problem.** `close_negotiation` auto-fills from the converged turn's
   raw content. The agent that closes first controls artifact quality. Both agents should know who is
   responsible for closing — and the closer should pass a cleaned `final_artifact`.
4. **No close coordination protocol.** Both agents see `converged=True` and race to close. First-mover wins
   and controls the artifact. Options: designated closer (initiator by default), or a separate
   `finalize_artifact` step before close.
5. **Autonomous loop works well.** `post_position → wait_for_turn → react → repeat` is natural. Blocking wait
   avoids polling. Hash-based acceptance is unambiguous.
6. **Counter-proposals are the best part.** Mapping requirements against actual coverage, identifying gaps,
   proposing thresholds with rationale, asking clarifying questions — all within the `counter` status.

### Synthesis

**What's confirmed working:**
- Round count, `artifact_content` on close, `accepting_hash` in turns — all present and useful
- Autonomous loop is solid — no human prompting needed between turns 3-5
- `join_negotiation` is demonstrably smoother (both agents said so independently)
- Hash-based acceptance is unambiguous; counter-proposal flow is natural

**Confirmed bugs (still open):**
- **Self-turns from `wait_for_turn`** — cc-tfc saw its own turn on the next `wait_for_turn` call. The
  iteration 2 fix filtered turns in the XREAD result, but the race may still occur when the peer is slow
  and the BLOCK timeout returns with only self-written turns.
- **Convergence detection race** — inconsistent across runs; timing issue in the accepting branch.

**New protocol gaps:**
- **Close coordination** — no designated closer; first-mover artifact wins. Initiator should close by default;
  SKILL.md should say so. `final_artifact` parameter should be documented as the lever for artifact quality.
- **Artifact extraction** — auto-fill writes the entire turn content. If the turn has preamble or Q&A before
  the actual agreement, the artifact is polluted. Options: (a) document that the closer should always pass
  `final_artifact` with the extracted section, or (b) add an `## ARTIFACT` section marker that the server
  extracts automatically.
- **`join_negotiation` as primary peer entry point** — SKILL.md still shows `read_latest` first in the
  manual loop section. Should promote `join_negotiation` to the main documented path.
- **`attach_evidence`** — nice-to-have for structured codebase references; low priority for now.
- **Turn budget during blocking wait** — cosmetic; low priority.

### Disposition

Open items carried forward:
- [ ] Investigate self-turns bug in `wait_for_turn` (confirmed still present)
- [ ] Investigate convergence detection race (may be related)
- [ ] SKILL.md: make initiator-closes-by-default explicit; document `final_artifact` for artifact quality
- [ ] SKILL.md: promote `join_negotiation` to primary peer entry path
- [ ] Decide on artifact extraction strategy: documented convention vs `## ARTIFACT` marker
- [ ] `attach_evidence` tool (deferred — nice to have)
