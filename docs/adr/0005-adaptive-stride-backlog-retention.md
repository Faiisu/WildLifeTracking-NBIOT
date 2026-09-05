# ADR 0005: Adaptive Stride Sampling for the Un-sent Location Backlog

## Status
Accepted

## Context
Ticket 09 (`.scratch/tracker-hardening/issues/09-buffer-fixes-during-lte-outage.md`)
adds an in-memory backlog so GPS fixes acquired during an LTE/MQTT outage are
retained and flushed once connectivity returns, instead of being discarded.
Backlog storage is a small fixed-capacity array in RAM (`CONFIG_TRACKER_BACKLOG_CAPACITY`,
default 40 entries of `struct tracker_location_fix`) — no flash persistence,
no dynamic allocation. With fixed capacity and a potentially unbounded outage
duration, some entries must eventually be discarded; the question this ADR
resolves is *which ones*, so the retained set still lets the animal's/asset's
route be reconstructed after a long outage rather than only showing where it
ended up.

Three candidate algorithms were designed and verified by simulation (Python
and, for the route-shape comparisons, a client-side JS re-implementation of
each) before writing any firmware code, after two of them were caught by
simulation producing behavior that did not match their design intent.

## Alternatives Considered

### 1. Flat active/anchor decimation cascade — rejected

Split the buffer into an "active zone" (recent fixes, thinned by keeping the
newer of each pair when full) and an "anchor zone" (meant to permanently
retain one representative fix per past overflow event, never decimated
again).

Simulated across 5,000,000 synthetic fixes (~47 years of continuous outage
at the tracker's 5-minute fix interval, deliberately far beyond any real
deployment, to expose asymptotic behavior): the retention lag (how far back
the oldest surviving fix reaches) **stabilizes at a small, fixed steady
state — roughly `2 x capacity` — within a few hundred fixes, and then never
grows again no matter how much longer the outage continues.** An "anchor"
promoted during decimation is typically evicted again on the very next
overflow, so it is not actually permanent in practice. A second attempt
using geometrically-growing per-level capacities in a multi-level cascade
hit the same ceiling (still flat at a fixed lag out to 5,000,000 events) —
this is a mathematical property of any fixed-capacity, iterative
pairwise-merge scheme, not an implementation bug: bounded memory storing
individually-merged samples cannot retain unboundedly old history while
also admitting new samples, regardless of how many cascade levels are used.

Route-shape impact: on a synthetic wandering path, every surviving point
clustered at the recent end of the outage; the early and middle portions of
the route had no representation at any resolution once the outage exceeded
roughly twice the buffer capacity in length.

### 2. Logarithmic stride buckets — rejected as the sole scheme

One bucket per power-of-two time-scale: bucket `k` is overwritten by the
newest fix whenever `fix_number % 2^k == 0`, with no merging or decimation
step. This genuinely avoids the steady-state ceiling above — simulated
reach kept growing across the full 5,000,000-fix run, reaching a
`2^(levels-1)` fix ceiling (e.g. ~524,288 fixes, about 5 years at the
tracker's fix interval, with only 20 levels/slots) rather than flattening
early.

Route-shape impact: waypoints traced the *entire* route shape back to the
start of the outage, which log-buckets alone do achieve — but each
"octave" of history is represented by exactly one point, so gaps between
consecutive surviving fixes grow geometrically further into the past. This
gives good overall shape recall but uneven, recency-biased density: half of
the total budget effectively represents only the most recent octave.

### 3. Adaptive stride sampling — chosen

A single global stride `S` (starts at 1): a fix is stored only if
`fix_number % S == 0`. When the fixed-capacity buffer fills, `S` doubles and
the existing buffer is refiltered to the new stride (dropping entries that
no longer satisfy it), freeing room to keep accepting new fixes at the
coarser stride.

Simulation showed the retained set stays **evenly spaced** (minimum and
maximum gaps between consecutive surviving fixes were identical at every
sampled point across the run) and the **span covered scales with elapsed
time** — at 1,000,000 simulated fixes with a 30-entry buffer, 15 surviving
waypoints spanned back to fix ~65,536, covering roughly 93% of the entire
outage, uniformly. Route-shape comparison (same synthetic path, same 30-slot
budget as the other two schemes) showed uniform coverage of the whole
recorded route rather than clustering at either the recent end (cascade) or
being recency-biased with geometrically widening gaps (log-buckets).

Trade-off accepted: unlike log-buckets, adaptive stride gives no extra
density specifically for the most recent fixes — the whole budget is spent
on uniform coverage of everything seen so far. If dense near-term detail is
later found to matter more than currently expected, a small fixed raw
window of the most recent N fixes can be added alongside the strided buffer
without changing this decision's core mechanism.

## Decision
Implement the backlog's overflow-handling policy in `tracker_backlog.c`
using **adaptive stride sampling**:

1. Maintain a single stride value `S`, initialized to 1.
2. For each newly-acquired-but-unsent fix with sequence number `n`: skip it
   (do not store) unless `n % S == 0`.
3. When a fix qualifies (`n % S == 0`) and the buffer is already at
   `CONFIG_TRACKER_BACKLOG_CAPACITY`: double `S`, then remove every buffered
   entry whose original sequence number no longer satisfies the new stride,
   before appending the qualifying fix.
4. On reconnect, flush the buffer oldest-to-newest as today's design already
   specifies (ticket 09), removing each entry only after its PUBACK is
   confirmed (QoS 1, ticket 04).

## Consequences
- **Route fidelity**: any deployment-length outage yields a backlog spread
  evenly across the whole outage, not just its most recent portion — the
  design goal that ruled out the flat cascade.
- **No permanent "first breadcrumb" guarantee**: unlike the original (and, per
  simulation, unachievable) goal of a literally-permanent anchor, the oldest
  surviving fix's age is proportional to total outage length; it is not
  pinned to the very first fix once the stride has doubled past it.
- **No extra recent-fix density**: if near-term resolution turns out to
  matter more in the field than expected, augment with a small fixed raw
  window (see ticket 09 comments) — a separate, additive change, not a
  rework of this decision.
- **Simulated, not field-verified**: the comparison above used a synthetic
  correlated random walk, not real GNSS traces or a real outage. A
  field-verification pass for backlog behavior under a real prolonged
  outage is still open (distinct from ticket 08's field test plan, which
  covers TAU/GNSS contention, not backlog retention).

## Comparisons and visualizations
Interactive simulations built during this decision (Claude artifacts,
private by default — share from each page's share menu if needed for a
report):
- Flat cascade step-through trace and its steady-state lag chart:
  https://claude.ai/code/artifact/523c4db4-10a9-4fff-9eaf-1aaee70c21bb
- Cascade vs. log-bucket retention-lag comparison across 5,000,000 fixes:
  https://claude.ai/code/artifact/0bfec00a-804e-4308-b47c-cf95e492d09c
- Route-shape comparison, log-bucket only (no raw window), with per-point
  gap chart: https://claude.ai/code/artifact/d81e1ccb-2918-45f1-9574-3194af874a24
- Route-shape comparison, adaptive stride (the chosen algorithm), with
  per-point gap chart: https://claude.ai/code/artifact/d50de598-3410-4ec5-9641-49553f6fc766
