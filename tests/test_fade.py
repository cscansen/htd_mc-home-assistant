"""Fade engine tests against a fake amp. Run: python3 tests/test_fade.py (no HA install needed)."""
import asyncio
import os
import sys
import time
import types

ROOT = os.path.join(os.path.dirname(__file__), "..", "custom_components")
sys.path.insert(0, os.path.abspath(ROOT))

# ── Minimal homeassistant stubs ──
ha = types.ModuleType("homeassistant")
for name in ["components", "components.media_player", "components.media_player.const", "const", "core",
             "helpers", "helpers.config_validation", "helpers.typing", "helpers.discovery"]:
    sys.modules["homeassistant." + name] = types.ModuleType("homeassistant." + name)
sys.modules["homeassistant"] = ha
mp = sys.modules["homeassistant.components.media_player"]


class MediaPlayerEntity:
    def async_write_ha_state(self):
        pass


class _Enum:
    SPEAKER = "speaker"
    SELECT_SOURCE = TURN_OFF = TURN_ON = VOLUME_MUTE = VOLUME_SET = VOLUME_STEP = 1


mp.MediaPlayerEntity = MediaPlayerEntity
mp.MediaPlayerDeviceClass = _Enum
sys.modules["homeassistant.components.media_player.const"].MediaPlayerEntityFeature = _Enum
c = sys.modules["homeassistant.const"]
c.STATE_OFF, c.STATE_ON, c.STATE_UNKNOWN = "off", "on", "unknown"
for k in ["CONF_HOST", "CONF_PORT", "CONF_NAME", "Platform"]:
    setattr(c, k, k)
sys.modules["homeassistant.core"].HomeAssistant = object
sys.modules["homeassistant.helpers.typing"].ConfigType = dict
class _Any(types.ModuleType):
    def __getattr__(self, name):
        return lambda *a, **k: (a[0] if a else None)


cv = _Any("homeassistant.helpers.config_validation")
sys.modules["homeassistant.helpers.config_validation"] = cv
sys.modules["homeassistant.helpers"].config_validation = cv
sys.modules["homeassistant.helpers.discovery"].load_platform = lambda *a, **k: None
try:
    import voluptuous  # noqa: F401
except ImportError:
    vol = types.ModuleType("voluptuous")
    vol.Schema = vol.Required = vol.Optional = vol.All = lambda *a, **k: (lambda x: x)
    vol.ALLOW_EXTRA = None
    sys.modules["voluptuous"] = vol

import importlib  # noqa: E402
htd = importlib.import_module("htd_mc")
media_player = importlib.import_module("htd_mc.media_player")
from htd_mc.htd_mc_client.models import ZoneDetail  # noqa: E402

media_player.SETTLE_SEC = 0.05
media_player.FADE_UP_SEC = 0.6
PARK = media_player.PARK_STEP


class FakeAmp:
    """One zone. Volume commands while off time out, like the real MCA-66."""
    timeouts = 0
    STALE = False  # real amp: a command's reply often shows the state from before the command

    def __init__(self, power=False, step=36, mute=False):
        self.power, self.step, self.mute = power, step, mute
        self.log = []
        self._flip = False

    def _reply(self, zone, before):
        self._flip = not self._flip
        return before if (FakeAmp.STALE and self._flip) else self._d(zone)

    def _d(self, zone):
        d = ZoneDetail(zone)
        d.power, d.mute, d.htd_volume, d.source = self.power, self.mute, self.step, 1
        d.volume = round(self.step / 60 * 100)
        d.number, d.mode, d.party, d.treble, d.bass, d.balance = zone, False, False, 0, 0, 0
        return d

    def _vol(self, zone, delta, name):
        self.log.append(name)
        if not self.power:
            FakeAmp.timeouts += 1
            raise TimeoutError("timed out")
        time.sleep(0.002)
        before = self._d(zone)
        self.step = max(0, min(60, self.step + delta))
        return self._reply(zone, before)

    def query_zone(self, zone):
        return self._d(zone)

    def volume_up(self, zone):
        return self._vol(zone, 1, "up")

    def volume_down(self, zone):
        return self._vol(zone, -1, "down")

    def power_on(self, zone):
        before = self._d(zone)
        self.log.append("on"); self.power = True
        return self._reply(zone, before)

    def power_off(self, zone):
        before = self._d(zone)
        self.log.append("off"); self.power = False; self.mute = False
        return self._reply(zone, before)

    def toggle_mute(self, zone):
        before = self._d(zone)
        self.log.append("mute")
        if getattr(self, "swallow_toggles", 0):  # client retry re-sent it: net no change
            self.swallow_toggles -= 1
            return self._reply(zone, before)
        self.mute = not self.mute
        return self._reply(zone, before)

    def set_source(self, zone, src):
        return self._d(zone)


class FakeHass:
    def __init__(self, loop):
        self.loop = loop

    def async_add_executor_job(self, fn, *a):
        return self.loop.run_in_executor(None, fn, *a)

    def async_create_task(self, coro):
        return self.loop.create_task(coro)


def make(amp):
    e = media_player.HtdDevice(0, 1, amp, {"sources": ["A"], "zones": ["Z"], "update_volume_on_change": False})
    e.hass = FakeHass(asyncio.get_running_loop())
    return e


async def settle(e):
    while e._fading:
        await asyncio.sleep(0.01)


FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


async def main():
    # 1. Off from 60%: walk down to park, then power off.
    amp = FakeAmp(power=True, step=36); e = make(amp)
    await e.async_turn_off()
    check("off parks then powers off", not amp.power and amp.step == PARK and amp.log[-1] == "off", amp.__dict__)
    check("off remembers pre-off level", e._resume_step == 36, e._resume_step)

    # 2. Volume while off: nothing sent to the amp.
    n = len(amp.log)
    await e.async_set_volume_level(0.5)
    check("volume while off not sent", len(amp.log) == n and e._resume_step == 30, amp.log[n:])

    # 3. On: returns once powered, then paced fade up to the stored volume.
    t0 = time.monotonic()
    await e.async_turn_on()
    check("turn_on returns at power-on", amp.power and amp.step == PARK and time.monotonic() - t0 < 0.3)
    await settle(e)
    dt = time.monotonic() - t0
    check("fade up reaches target", amp.step == 30, amp.step)
    check("fade up is paced (~FADE_UP_SEC)", 0.5 < dt < 1.2, f"{dt:.2f}s")

    # 4. Retarget mid fade-up.
    await e.async_turn_off()
    await e.async_turn_on()
    await asyncio.sleep(0.1)
    await e.async_set_volume_level(0.6)
    await settle(e)
    check("retarget mid fade", amp.step == 36, amp.step)

    # 5. Off mid fade-up -> fade down from current, power off parked.
    await e.async_turn_off()
    await e.async_turn_on()
    await asyncio.sleep(0.2)
    await e.async_turn_off()
    check("off mid fade-up parks + off", not amp.power and amp.step == PARK, amp.__dict__)

    # 6. Loud wake (e.g. HA restarted mid-fade): mute, park, unmute; no resume -> hold at park.
    amp = FakeAmp(power=False, step=50); e = make(amp)
    await e.async_turn_on()
    await settle(e)
    check("loud wake parked", amp.power and amp.step == PARK and not amp.mute, amp.__dict__)
    check("loud wake muted then unmuted", amp.log.count("mute") == 2, amp.log.count("mute"))
    i_mute = amp.log.index("mute")
    check("muted before walking down", "down" in amp.log[i_mute:], amp.log[:5])

    # 7. On during fade-down -> fade back up to the previous target.
    amp = FakeAmp(power=True, step=36); e = make(amp)
    off = asyncio.ensure_future(e.async_turn_off())
    await asyncio.sleep(0.01)
    await e.async_turn_on()
    await off
    await settle(e)
    check("on during fade-down returns to target", amp.power and amp.step == 36, amp.__dict__)

    # 8. Never a volume command to an off zone, in any scenario above.
    check("no volume command ever sent to an off zone", FakeAmp.timeouts == 0, FakeAmp.timeouts)

    # 9. A failing command doesn't wedge the entity (the old stuck _ramping bug).
    amp = FakeAmp(power=True, step=20); e = make(amp)
    real = amp.volume_up
    amp.volume_up = lambda z: (_ for _ in ()).throw(OSError("boom"))
    await e.async_set_volume_level(0.6)
    await settle(e)
    amp.volume_up = real
    await e.async_set_volume_level(0.6)
    await settle(e)
    check("recovers after a failed fade", amp.step == 36, amp.step)

    # 10. Mute service is idempotent (amp only toggles).
    await e.async_mute_volume(False)
    check("mute(False) on unmuted zone sends nothing", "mute" not in amp.log)
    await e.async_mute_volume(True)
    await e.async_mute_volume(True)
    check("mute(True) twice toggles once", amp.log.count("mute") == 1 and amp.mute)

    # 11. Loud wake where the first mute toggle is swallowed (client retry double-send).
    amp = FakeAmp(power=False, step=50); amp.swallow_toggles = 1; e = make(amp)
    muted_during_walk = []
    real_down = amp.volume_down
    amp.volume_down = lambda z: (muted_during_walk.append(amp.mute), real_down(z))[1]
    await e.async_turn_on()
    await settle(e)
    check("swallowed toggle: still muted for the whole walk-down",
          muted_during_walk and all(muted_during_walk), muted_during_walk[:3])
    check("swallowed toggle: unmuted at the end", amp.power and amp.step == PARK and not amp.mute, amp.__dict__)


for stale in (False, True):
    FakeAmp.STALE = stale
    print(f"── replies {'stale every other command' if stale else 'honest'} ──")
    asyncio.run(main())
print("FAILED:", FAILS if FAILS else "none")
sys.exit(1 if FAILS else 0)
