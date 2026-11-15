### 1.3.0 - November 15, 2026
- Zones fade instead of jumping. Turning a zone off lowers it to 12% before it powers off, and turning it on
  raises it smoothly (about 4.5 s) back to its earlier volume, or to the volume you ask for.
- Changing the volume while a zone is off no longer breaks that zone's volume control. The new volume is used the
  next time the zone turns on.
- If a zone comes on loud (for example after a restart during a fade), it is muted and turned down before it fades
  up.
- Mute on/off now does what you ask; before, it always flipped.
- Commands to the amp no longer collide, so there are no more "bad response, will retry" warnings.

### 1.2.0 - TBD 
- Upgrading to HASS 2026.6.4
- New client. Better support for volume changes and bass, treble and balance. ([#5](https://github.com/hikirsch/htd_mc-home-assistant/issues/5)).

### 1.1.0 - March 31, 2024 
- Upgrading to support HASS 2024.1.0 ([#7](https://github.com/hikirsch/htd_mc-home-assistant/issues/7)). 
- Adding support for HACS

### 1.0.0 - Previous log
- July 18, 2021 - Add version in manifest.json to support 2021.6+
- April 7, 2020 - Changed Icon and added unique_id (allows editing name and entity ID in Home Assistant UI)
- April 8, 2020 - Support multiple MCAs
- April 6, 2020 - Initial release

