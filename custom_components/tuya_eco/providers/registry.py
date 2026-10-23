"""Provider registry.

To add a new ecosystem, implement `BaseProvider` in a new module and register
the class here. Nothing else in the integration needs to change.
"""

from __future__ import annotations

from .base import BaseProvider


def _providers() -> dict[str, type[BaseProvider]]:
    # Imported lazily so that optional dependencies of one provider never break
    # the others (and to keep import time of the integration small).
    from .ledvance import LedvanceProvider, SylvaniaProvider
    from .manual import ManualProvider
    from .smartlife import SmartLifeProvider
    from .tuya_iot import TuyaIotProvider
    from .tuya_oem import TuyaOemProvider

    classes: list[type[BaseProvider]] = [
        SmartLifeProvider,
        LedvanceProvider,
        SylvaniaProvider,
        TuyaIotProvider,
        TuyaOemProvider,
        ManualProvider,
    ]
    return {cls.key: cls for cls in classes}


def get_provider_class(key: str) -> type[BaseProvider]:
    """Return the provider class registered under `key`."""
    try:
        return _providers()[key]
    except KeyError as err:
        raise KeyError(f"Unknown provider '{key}'") from err


def provider_choices() -> dict[str, str]:
    """Return {key: display name} for the config flow."""
    return {key: cls.name for key, cls in _providers().items()}
