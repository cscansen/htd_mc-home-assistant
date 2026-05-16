"""Support for HTD MC Series"""

import logging

from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerDeviceClass
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from . import DOMAIN
from .htd_mc_client.client import HtdMcClient
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

_LOGGER = logging.getLogger(__name__)


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
    changing_volume: int | None = None
    _ramping: bool = False
    zone_info: ZoneDetail = None

    def __init__(self, device_instance_id, zone, client, config):
        self.device_instance_id = device_instance_id
        self.zone = zone
        self.client = client
        self.sources = config["sources"]
        self.update_volume_on_change = config["update_volume_on_change"]
        # zones are 0 based in the config b/c it's an array
        self.zone_name = config["zones"][zone - 1]
        self.update()

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

    def update(self):
        _LOGGER.debug("starting updating zone %d" % self.zone)
        self.zone_info = self.client.query_zone(self.zone)
        _LOGGER.debug(
            "got new update for Zone %d, zone_info = %s" % (self.zone, self.zone_info)
        )

    @property
    def state(self):
        if self.zone_info.power is None:
            return STATE_UNKNOWN
        if self.zone_info.power:
            return STATE_ON
        return STATE_OFF

    def turn_on(self):
        self.client.power_on(self.zone)

    def turn_off(self):
        self.client.power_off(self.zone)

    @property
    def volume_level(self) -> float | None:
        if self.zone_info is None:
            return None
        if self.changing_volume is not None:
            return self.changing_volume / 100
        return self.zone_info.volume / 100

    async def async_set_volume_level(self, volume: float):
        target = int(volume * 100)
        self.changing_volume = target
        self.async_write_ha_state()

        if self._ramping:
            return

        self._ramping = True
        await self.hass.async_add_executor_job(self._ramp_volume)
        self._ramping = False
        self.async_write_ha_state()

    def _ramp_volume(self):
        def on_increment(desired: int, zone_info: ZoneDetail) -> int | None:
            self.zone_info = zone_info
            if self.changing_volume != desired:
                return self.changing_volume
            return None

        self.client.set_volume(self.zone, self.changing_volume, on_increment)
        self.zone_info = self.client.query_zone(self.zone)
        self.changing_volume = None

    @property
    def is_volume_muted(self) -> bool | None:
        if self.zone_info is None:
            return None
        return self.zone_info.mute

    def mute_volume(self, mute):
        self.client.toggle_mute(self.zone)

    @property
    def source(self) -> int:
        return self.sources[self.zone_info.source - 1]

    @property
    def source_list(self):
        return self.sources

    @property
    def media_title(self):
        return self.source

    def select_source(self, source: int):
        index = self.sources.index(source)
        self.client.set_source(self.zone, index + 1)

    @property
    def icon(self):
        return "mdi:disc-player"
