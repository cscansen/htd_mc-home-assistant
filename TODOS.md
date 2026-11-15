# htd_mc — TODOs

- [x] Bug: a volume_set to an off zone left `_ramping` stuck True (fixed in 1.3.0; Garage cleared by the HA restart
  on 2026-11-15).
- [x] Zone volume fade: built in 1.3.0 (`docs/volume_fade_design.md`).
- [x] `_set_mute` confirm-and-retry loaded at the HA restart on 2026-11-15.
- [ ] **The loud-wake backstop can't use mute: on the MCA-66, a volume step while muted cancels the mute**
  (checked directly 2026-11-15: volume_up while muted unmuted and didn't change the volume). So the step-down is
  audible, though it still parks. Planned fix: during the step-down, switch the zone to an empty input (volume
  steps work there; tested on Source 6), then switch back. Needs the user to say which input has nothing
  connected, plus an HA restart.
- [ ] Listen once on a real zone: whether the 1 s settle after power-on is enough (no clipped start), and whether the
  fade down and fade up sound smooth.
