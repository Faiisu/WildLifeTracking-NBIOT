# 02: Purge secrets from git history and clean up repo clutter

**What to build:** A fresh clone of the repo from the GitHub remote contains no credential material in any commit, past or present. Alongside the history rewrite, remove tracked cruft that has accumulated in the repo: `.DS_Store` files, and the duplicate `l4/` exercise tree that `AGENTS.md` already says to prefer `l4_MQTT/` over.

**Blocked by:** None — 01 is resolved. Note: the user waived the password rotation (see ticket 01), so the old value being purged from history is still technically live at the broker; this rewrite removes it from the repo but does not itself invalidate it.

**Status:** resolved

- [x] The MQTT credential previously committed in ESP32 `config.py` is not present in any reachable commit after the rewrite — verified by scanning all reachable git blobs on `origin/main` for the leaked string (also caught and scrubbed an accidental copy that ended up in this ticket's own file during triage)
- [x] All `.DS_Store` files are removed from tracking and ignored going forward
- [x] The `l4/` directory (superseded by `l4_MQTT/`) is deleted from the tree; `AGENTS.md` no longer mentions it
- [x] The rewritten history has been force-pushed to `origin` (confirmed `origin/main` == local rewritten `main`)
- [x] Re-cloning the repo now gets the cleaned tree; note one extra commit (`moving files into folder`, adding `v3.3.3_practices/`) landed on `origin/main` alongside the push, made outside this ticket's scope

## Comments

Rewrote history with `git filter-repo` in two passes: (1) `--invert-paths` on
the ESP32 `config.py` path, (2) `--replace-text` to scrub the literal
credential string, after discovering it had also been pasted into
`01-rotate-mqtt-credentials.md` during triage — a good reminder to never
quote a live secret verbatim in a ticket. A pre-rewrite backup bundle was
kept locally before the force-push in case anything needed recovery. Force-push
was done by the user (blocked for the agent by the auto-mode classifier, as
expected for that kind of action). Verified post-push against `origin/main`
directly rather than trusting the rewrite alone.
