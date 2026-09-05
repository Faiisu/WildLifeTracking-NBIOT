# 02: Purge secrets from git history and clean up repo clutter

**What to build:** A fresh clone of the repo from the GitHub remote contains no credential material in any commit, past or present. Alongside the history rewrite, remove tracked cruft that has accumulated in the repo: `.DS_Store` files, and the duplicate `l4/` exercise tree that `AGENTS.md` already says to prefer `l4_MQTT/` over.

**Blocked by:** None — 01 is resolved. Note: the user waived the password rotation (see ticket 01), so the old value being purged from history is still technically live at the broker; this rewrite removes it from the repo but does not itself invalidate it.

**Status:** ready-for-agent

- [ ] The MQTT credential previously committed in ESP32 `config.py` is not present in any reachable commit after the rewrite
- [ ] All `.DS_Store` files are removed from tracking and ignored going forward
- [ ] The `l4/` directory (superseded by `l4_MQTT/`) is deleted from the tree
- [ ] The rewritten history has been force-pushed to `origin`, and `AGENTS.md` guidance about `l4_MQTT/` no longer needs the caveat about a duplicate `l4/`
- [ ] A collaborator re-cloning the repo ends up with an identical working tree to before the cleanup (minus the removed files/secrets)
