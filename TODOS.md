# htd_mc — TODOs

- [x] Bug: a volume_set to an off zone left `_ramping` stuck True (fixed in 1.3.0; Garage cleared by the HA restart
  on 2026-11-15).
- [x] Zone volume fade: built in 1.3.0 (`docs/volume_fade_design.md`).
- [ ] The `_set_mute` confirm-and-retry fix (from the live backstop test) is on HA but **loads at the next HA
  restart**. Until then the loud-wake backstop can park a zone without muting it first (audible, still parks).
- [ ] Listen once on a real zone: whether the 1 s settle after power-on is enough (no clipped start), and whether the
  fade down and fade up sound smooth.
