# Zone volume fade — design (2026-11-14, not built)

Review page with the open decisions: https://claude.ai/artifact/XtScpUhiwrKu9X6ZWsWgH8

## Problem
A zone powers on at its last volume, and the amp ignores volume commands while a zone is off, so a zone
turned on after loud use starts loud. Fade each zone down to a parked level before power-off, then fade it up
after power-on.

## Decided (user, 2026-11-14)
- The fade lives in this integration (`htd_mc`), not in Dynamic Central Audio, so it covers every HA path
  (DCA, dashboards, HomeKit, automations).
- Parked level is **12% for all zones**.
- Fade up takes 4–5 s; the target for fade down was 2 s (see "Open").
- No wall keypads are installed (HA-only control), so there is no keypad fallback.

## Lifecycle
on → fade down → park at 12% → power off → power on (wakes at 12%) → settle about 1 s → fade up to the target.
A new volume target during a fade re-targets it (this reuses `changing_volume`). An off during a fade up
switches to the fade down from the current level.

## Phase 0 measurements (Garage, zone 2, direct to the amp)
| Check | Result |
|---|---|
| Deployed code vs repo | Identical (only the manifest version differs: 1.2.0 vs 1.2.2) |
| One volume step (1/60, ~1.7%) | 0.14–0.16 s |
| 12% → 60% | 4.46 s; 60% → 30% takes 2.79 s |
| Volume command while off | The amp does not respond: `TimeoutError` on recv |
| Wake volume | The last volume, as expected |
| Mute | Toggle only (`toggle_mute`); cleared by a power cycle |
| Concurrent senders | One "Bad response, will retry", recovered by the retry |

So a 2 s fade down can cover only about 13 steps.

## Built in regardless
- Never send volume to an off zone. Check `power` first.
- Reset the "fade in progress" state in `try/finally`. See the bug below.
- One lock per amp client, so status polling and fade commands are serialized.
- Pace the fade up: run at the amp's top speed for large moves and add a delay between steps for small
  ones, so every fade up takes about 4 s.
- No instant (no-fade) path is needed. Its only planned caller, the PTT intercom, is parked as of 2026-11-14.
  Add one only if a future caller needs volume right away.

## Open (on the review page)
- Off sequence: either a 2 s audible fade, then mute and a silent walk down to 12%, then power off (recommended),
  or a full audible fade of up to about 4.5 s.
- Backstop for a zone that wakes loud (an HA restart or power loss mid-fade): mute, walk down, unmute, fade up.
