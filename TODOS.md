# htd_mc — TODOs

- [ ] **Bug: a volume_set to an off zone leaves `_ramping` stuck True** (`media_player.py:110-120`, no try/finally).
  `client.set_volume` raises `TimeoutError` (the amp ignores volume commands while off). After that, every volume_set
  on that entity returns at once and does nothing, and `volume_level` reports the requested value. The only way to
  clear it is an HA restart (YAML platform, no reload). Hit on **Garage 2026-11-14** during fade Phase 0 testing;
  the Garage HA volume stays broken until HA restarts.
- [ ] Zone volume fade: design in `docs/volume_fade_design.md`; waiting on the open decisions.
