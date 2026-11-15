"""Support for HTD MC Series"""

import asyncio
import logging
import time

from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerDeviceClass
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from . import DOMAIN
from .htd_mc_client.client import HtdMcClient
from .htd_mc_client.constants import HtdConstants
from .htd_mc_client.models import ZoneDetail

MEDIA_PLAYER_PREFIX = "media_player.htd_mc_zone"

SUPPORT_HTD_MC = (
    MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
)

# ── Volume fade (docs/volume_fade_design.md) ──────────────────────────────────────────────
# The amp has no fade and ignores volume commands while a zone is off (they time out), and a
# zone wakes at its last volume. So every zone is walked down to PARK before power-off and
# walked up from there after power-on, one HTD step (1/60) per command (~0.15 s each).
PARK_PERCENT = 12          # parked level, all zones (user 2026-11-14)
LOUD_WAKE_MARGIN = 3       # HTD steps above park that count as "woke loud" -> backstop
SILENT_SOURCE = 6          # amp input with nothing connected (user 2026-11-15: only 1 and 2 are wired)
SETTLE_SEC = 1.0           # after power-on, before the fade up starts
FADE_UP_SEC = 4.5          # paced fade up after power-on; capped by amp speed for big moves
POWER_WAIT_SEC = 10.0      # how long turn_on waits for the zone to power up
OFF_WAIT_SEC = 20.0        # how long turn_off waits for the fade down + power-off
MAX_LOOP_STEPS = 400       # hard stop for one move
STALL_LIMIT = 3            # confirmed no-progress steps before giving up

_LOGGER = logging.getLogger(__name__)


def pct_to_step(percent: float) -> int:
    return max(0, min(HtdConstants.MAX_HTD_VOLUME,
                      round(percent * HtdConstants.MAX_HTD_VOLUME / 100)))


def step_to_pct(step: int) -> int:
    return round(step / HtdConstants.MAX_HTD_VOLUME * 100)


PARK_STEP = pct_to_step(PARK_PERCENT)


def setup_platform(hass: HomeAssistant, config, add_entities, discovery_info=None):
    htd_configs = hass.data[DOMAIN]
    entities = []

    for device_index in range(len(htd_configs)):
        config = htd_configs[device_index]
        zones = config["zones"]
        client = config["client"]

        for zone_index in range(len(zones)):
            entity = HtdDevice(device_index, zone_index + 1, client, config)
            entities.append(entity)

    add_entities(entities)


class HtdDevice(MediaPlayerEntity):
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER

    device_instance_id: int = None
    client: HtdMcClient = None
    sources: [str] = None
    zone: int = None
    zone_info: ZoneDetail = None

    def __init__(self, device_instance_id, zone, client, config):
        self.device_instance_id = device_instance_id
        self.zone = zone
        self.client = client
        self.sources = config["sources"]
        self.update_volume_on_change = config["update_volume_on_change"]
        # zones are 0 based in the config b/c it's an array
        self.zone_name = config["zones"][zone - 1]
        # Desired state. The fade task drives the amp toward it, re-reading it every step, so a
        # new volume or an off/on mid-fade just changes where the next step goes.
        self._want_power: bool | None = None
        self._target_step: int | None = None   # volume while on
        self._resume_step: int | None = None   # volume to fade up to at the next power-on
        self._fade_task: asyncio.Task | None = None
        self._powered = asyncio.Event()
        self.update()
        if self.zone_info is not None:
            self._want_power = bool(self.zone_info.power)

    @property
    def enabled(self) -> bool:
        return self.zone_info is not None

    @property
    def supported_features(self):
        return SUPPORT_HTD_MC

    @property
    def unique_id(self):
        return f"{MEDIA_PLAYER_PREFIX}_{self.device_instance_id}_{self.zone}"

    @property
    def name(self):
        return self.zone_name

    @property
    def _fading(self) -> bool:
        return self._fade_task is not None and not self._fade_task.done()

    def update(self):
        _LOGGER.debug("starting updating zone %d" % self.zone)
        info = self.client.query_zone(self.zone)
        if info is not None:
            self.zone_info = info
            if not self._fading and info.power is not None:
                # Nothing of ours in flight: follow the amp (e.g. it lost power).
                self._want_power = bool(info.power)
        _LOGGER.debug(
            "got new update for Zone %d, zone_info = %s" % (self.zone, self.zone_info)
        )

    @property
    def state(self):
        if self.zone_info is None or self.zone_info.power is None:
            return STATE_UNKNOWN
        if self.zone_info.power:
            return STATE_ON
        return STATE_OFF

    @property
    def volume_level(self) -> float | None:
        if self.zone_info is None:
            return None
        # While a fade is in flight, report where it is going (as the old ramp did), so callers
        # that re-assert a volume (Dynamic Central Audio, every 60 s) see no difference.
        if self._want_power and self._target_step is not None and self._fading:
            return step_to_pct(self._target_step) / 100
        return self.zone_info.volume / 100

    @property
    def is_volume_muted(self) -> bool | None:
        if self.zone_info is None:
            return None
        return self.zone_info.mute

    @property
    def source(self) -> int:
        return self.sources[self.zone_info.source - 1]

    @property
    def source_list(self):
        return self.sources

    @property
    def media_title(self):
        return self.source

    @property
    def icon(self):
        return "mdi:disc-player"

    @property
    def extra_state_attributes(self):
        return {"fading": self._fading}

    def select_source(self, source: int):
        index = self.sources.index(source)
        self.client.set_source(self.zone, index + 1)

    # ── Services ────────────────────────────────────────────────────────────────────────

    async def async_turn_on(self):
        already_on = bool(self.zone_info and self.zone_info.power) and self._want_power
        self._want_power = True
        if already_on and not self._fading:
            return
        if not self._fading or self._target_step is None:
            # Fade up to the pre-off level, or to what was asked for while the zone was off.
            self._target_step = self._resume_step
        self._resume_step = None
        self._powered.clear()
        self._ensure_fade()
        try:
            await asyncio.wait_for(self._powered.wait(), POWER_WAIT_SEC)
        except asyncio.TimeoutError:
            _LOGGER.warning("Zone %d: no power-on after %.0f s", self.zone, POWER_WAIT_SEC)

    async def async_turn_off(self):
        info = self.zone_info
        if self._want_power is not False and info is not None and info.power:
            if self._fading and self._target_step is not None:
                self._resume_step = self._target_step
            else:
                self._resume_step = info.htd_volume
        self._want_power = False
        self._ensure_fade()
        task = self._fade_task
        if task is not None:
            done, _ = await asyncio.wait({task}, timeout=OFF_WAIT_SEC)
            if not done:
                _LOGGER.warning("Zone %d: fade down still running after %.0f s", self.zone, OFF_WAIT_SEC)

    async def async_set_volume_level(self, volume: float):
        step = pct_to_step(volume * 100)
        powered = bool(self.zone_info and self.zone_info.power)
        if not self._want_power or (not powered and not self._fading):
            # Off or shutting down: the amp ignores (times out on) volume while off. Keep it
            # for the next power-on instead of sending it.
            self._resume_step = step
            self.async_write_ha_state()
            return
        self._target_step = step
        self._ensure_fade()
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool):
        # The amp only has a mute toggle; only toggle when the state differs.
        if self.zone_info is None or not self.zone_info.power:
            return
        await self._set_mute(mute)
        self.async_write_ha_state()

    async def _to_silent_source(self) -> int | None:
        """Switch to SILENT_SOURCE (nothing connected) and return the original source, or None."""
        info = await self._cmd(self.client.query_zone)
        if info is None or not info.power or info.source == SILENT_SOURCE:
            return None
        orig = info.source
        for _ in range(3):
            await self.hass.async_add_executor_job(self.client.set_source, self.zone, SILENT_SOURCE)
            info = await self._cmd(self.client.query_zone)
            if info is not None and info.source == SILENT_SOURCE:
                return orig
        _LOGGER.warning("Zone %d: silent input not confirmed; parking audibly", self.zone)
        return orig  # still restore: one of the attempts may have landed late

    async def _restore_source(self, orig: int | None):
        if orig is None:
            return
        for _ in range(3):
            info = await self._cmd(self.client.query_zone)
            if info is None or not info.power or info.source == orig:
                return
            await self.hass.async_add_executor_job(self.client.set_source, self.zone, orig)
        _LOGGER.warning("Zone %d: could not restore source %d", self.zone, orig)

    async def _set_mute(self, mute: bool) -> bool:
        """Drive the toggle-only mute to `mute`, confirming by query. The client re-sends a command
        after a garbled reply, and a re-sent toggle cancels itself, so check and retry."""
        for _ in range(3):
            info = await self._cmd(self.client.query_zone)
            if info is None or not info.power:
                return False
            if bool(info.mute) == mute:
                return True
            await self._cmd(self.client.toggle_mute)
        info = await self._cmd(self.client.query_zone)
        return bool(info and info.mute) == mute

    # ── Fade engine ─────────────────────────────────────────────────────────────────────

    def _ensure_fade(self):
        if not self._fading:
            self._fade_task = self.hass.async_create_task(self._async_fade())
            self._fade_task.add_done_callback(lambda _t: self.async_write_ha_state())

    async def _cmd(self, fn):
        info = await self.hass.async_add_executor_job(fn, self.zone)
        if info is None:
            info = await self.hass.async_add_executor_job(self.client.query_zone, self.zone)
        if info is not None:
            self.zone_info = info
        return info

    async def _query(self):
        return await self._cmd(self.client.query_zone)

    async def _move_to(self, get_target, paced_since=None, abort=lambda: False):
        """Step toward get_target() one HTD step per command until there.

        A step command's reply can show the state from *before* the step (the parser takes the
        first record for the zone), so replies are only a hint: whenever a reply says "arrived"
        or "didn't move", confirm with a real query. Give up after STALL_LIMIT confirmed stalls."""
        stalls = 0
        for _ in range(MAX_LOOP_STEPS):
            if abort():
                return
            info = self.zone_info
            target = get_target()
            if info is None or not info.power or target is None:
                return
            cur = info.htd_volume
            if cur == target:
                info = await self._query()
                if info is None or not info.power or info.htd_volume == target:
                    return
                continue
            t0 = time.monotonic()
            await self._cmd(self.client.volume_up if target > cur else self.client.volume_down)
            self.async_write_ha_state()
            if self.zone_info.htd_volume == cur:
                await self._query()
                if self.zone_info.htd_volume == cur:
                    stalls += 1
                    if stalls >= STALL_LIMIT:
                        _LOGGER.warning("Zone %d: volume stuck at step %d", self.zone, cur)
                        return
                    continue
            stalls = 0
            if paced_since is not None:
                target = get_target()
                remaining = abs(target - self.zone_info.htd_volume) if target is not None else 0
                time_left = FADE_UP_SEC - (time.monotonic() - paced_since)
                if remaining and time_left > 0:
                    delay = time_left / remaining - (time.monotonic() - t0)
                    if delay > 0:
                        await asyncio.sleep(delay)
        _LOGGER.warning("Zone %d: fade hit the %d-step limit", self.zone, MAX_LOOP_STEPS)

    async def _async_fade(self):
        silent_from = None  # original source while parked on the silent input
        try:
            await self._query()
            for _ in range(8):  # passes: each one is a whole fade down, power change or fade up
                info = self.zone_info
                if info is None or info.power is None:
                    return
                if not self._want_power:
                    # Off: full audible fade to PARK, then power off (user 2026-11-14).
                    if not info.power:
                        return
                    await self._move_to(lambda: PARK_STEP, abort=lambda: bool(self._want_power))
                    if self._want_power:
                        continue  # turned back on mid-fade: next pass fades up again
                    await self._cmd(self.client.power_off)
                    await self._query()
                    if self.zone_info and self.zone_info.power and not self._want_power:
                        continue  # power-off not confirmed: try again
                    return
                if not info.power:
                    await self._cmd(self.client.power_on)
                    info = await self._query()  # the power_on reply can be stale
                    if not (info and info.power):
                        _LOGGER.warning("Zone %d: power_on not confirmed", self.zone)
                        continue
                    if info.htd_volume > PARK_STEP + LOUD_WAKE_MARGIN:
                        # Woke loud (HA restart or power loss mid-fade): park it in silence. Mute
                        # can't do it (the amp cancels mute on any volume step), so switch to an
                        # input with nothing connected, step down, then switch back.
                        _LOGGER.info("Zone %d woke loud (step %d); parking before fade-up",
                                     self.zone, info.htd_volume)
                        silent_from = await self._to_silent_source()
                        await self._move_to(lambda: PARK_STEP)
                        await self._restore_source(silent_from)
                        silent_from = None
                    self._powered.set()
                    self.async_write_ha_state()
                    await asyncio.sleep(SETTLE_SEC)
                    await self._move_to(lambda: self._target_step, paced_since=time.monotonic(),
                                        abort=lambda: not self._want_power)
                    continue  # re-check: an off may have arrived during the fade up
                self._powered.set()
                await self._move_to(lambda: self._target_step, abort=lambda: not self._want_power)
                if self._want_power:
                    return
        except Exception:  # noqa: BLE001 - a dead fade must never leave the entity wedged
            _LOGGER.exception("Zone %d: fade failed", self.zone)
        finally:
            if silent_from is not None:
                try:
                    await self._restore_source(silent_from)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Zone %d: could not restore source after backstop", self.zone)
            self._powered.set()
            self.async_write_ha_state()
