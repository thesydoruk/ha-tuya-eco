"""Base entity."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .hub import EcoHub
from .local.device import EcoDevice
from .local.protocol import TuyaLocalError
from .mapping import EntityDef

_LOGGER = logging.getLogger(__name__)


class EcoEntity(Entity):
    """Entity bound to one EcoDevice and one EntityDef."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub: EcoHub, device: EcoDevice, definition: EntityDef) -> None:
        """Initialise."""
        self.hub = hub
        self.device = device
        self.definition = definition
        self._attr_unique_id = f"{device.id}_{definition.key}"
        self._attr_name = definition.name
        if definition.name is None:
            self._attr_name = None  # takes the device name
        if definition.device_class:
            self._attr_device_class = definition.device_class  # type: ignore[assignment]
        if definition.entity_category:
            self._attr_entity_category = EntityCategory(definition.entity_category)
        if definition.icon:
            self._attr_icon = definition.icon
        if definition.translation_key:
            self._attr_translation_key = definition.translation_key
        if definition.meta.get("enabled_default") is False:
            self._attr_entity_registry_enabled_default = False
        info = device.info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id)},
            name=info.name,
            manufacturer=hub.provider.name.split(" (")[0],
            model=info.product_name or info.product_id or None,
            model_id=info.product_id or None,
        )

    @property
    def available(self) -> bool:
        """Available while the LAN link is up (or cloud fallback is possible)."""
        return self.device.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to device updates."""
        self.async_on_remove(self.device.async_add_listener(self._device_updated))

    @callback
    def _device_updated(self) -> None:
        self.async_write_ha_state()

    # ------------------------------------------------------------------ DP helpers

    def dp(self, role: str) -> int | None:
        """DP id for a role."""
        return self.definition.dps.get(role)

    def value(self, role: str) -> Any:
        """Current value of the DP bound to `role`."""
        dp_id = self.dp(role)
        return None if dp_id is None else self.device.get_dp(dp_id)

    async def async_write_dps(self, dps: dict[int | str, Any]) -> None:
        """Write DPs, locally first, through the cloud when the LAN link is down."""
        try:
            await self.device.async_set_dps(dps)
            return
        except TuyaLocalError as err:
            if not self.hub.cloud_fallback:
                raise HomeAssistantError(f"{self.device.name}: {err}") from err
            _LOGGER.debug("%s: LAN write failed (%s), trying cloud", self.device.name, err)
        if not await self.hub.async_send_cloud(self.device, dps):
            raise HomeAssistantError(f"{self.device.name}: device unreachable (LAN and cloud)")

    async def async_write(self, role: str, value: Any) -> None:
        """Write a single role."""
        dp_id = self.dp(role)
        if dp_id is None:
            raise HomeAssistantError(f"{self.device.name}: no DP for {role}")
        await self.async_write_dps({dp_id: value})

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the raw DP for debugging on the primary entity only."""
        return None
