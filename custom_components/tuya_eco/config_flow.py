"""Config flow: pick a provider, log in (credentials / keys / QR code), set options."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_PROVIDER,
    CONF_PROVIDER_DATA,
    DEFAULT_CLOUD_FALLBACK,
    DEFAULT_CLOUD_REFRESH_HOURS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    OPT_CLOUD_FALLBACK,
    OPT_CLOUD_REFRESH_HOURS,
    OPT_POLL_INTERVAL,
)
from .providers.base import (
    BaseProvider,
    LoginKind,
    LoginStep,
    ProviderAuthError,
    ProviderError,
    ProviderLoginPending,
)
from .providers.registry import get_provider_class, provider_choices

_LOGGER = logging.getLogger(__name__)


def _field_schema(kind: str) -> Any:
    """Translate a provider field spec into a selector."""
    if kind == "password":
        return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
    if kind.startswith("select:"):
        options = [o for o in kind[len("select:") :].split(",") if o]
        return selector.SelectSelector(
            selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
        )
    return selector.TextSelector()


def _login_schema(provider: type[BaseProvider]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    for name, kind in provider.login_fields.items():
        optional = kind.endswith("?")
        kind = kind.rstrip("?")
        key = vol.Optional(name) if optional else vol.Required(name)
        if kind.startswith("select:"):
            default = kind[len("select:") :].split(",")[0]
            key = vol.Optional(name, default=default) if optional else vol.Required(name, default=default)
        fields[key] = _field_schema(kind)
    return vol.Schema(fields)


class TuyaEcoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._provider_key: str | None = None
        self._provider: BaseProvider | None = None
        self._login_step: LoginStep | None = None
        self._reauth_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TuyaEcoOptionsFlow:
        """Options."""
        return TuyaEcoOptionsFlow()

    # ------------------------------------------------------------------ steps

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the provider."""
        if user_input is not None:
            self._provider_key = user_input[CONF_PROVIDER]
            return await self.async_step_login()
        choices = provider_choices()
        schema = vol.Schema(
            {
                vol.Required(CONF_PROVIDER, default=next(iter(choices))): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value=k, label=v) for k, v in choices.items()],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_login(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the provider's login fields."""
        assert self._provider_key is not None
        cls = get_provider_class(self._provider_key)
        errors: dict[str, str] = {}
        if user_input is not None:
            self._provider = cls(self.hass)
            try:
                self._login_step = await self._provider.async_start_login(user_input)
                if self._login_step.kind == LoginKind.QR_CODE:
                    return await self.async_step_scan()
                await self._provider.async_finish_login(self._login_step.state)
                return await self._async_finish()
            except AbortFlow:
                raise
            except ProviderAuthError as err:
                _LOGGER.debug("auth failed: %s", err)
                errors["base"] = "auth_failed"
            except ProviderError as err:
                _LOGGER.debug("provider error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("unexpected error during login")
                errors["base"] = "unknown"
        return self.async_show_form(
            step_id="login",
            data_schema=_login_schema(cls),
            errors=errors,
            description_placeholders={"provider": cls.name},
        )

    async def async_step_scan(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the QR code and wait for the scan."""
        assert self._provider is not None and self._login_step is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._provider.async_finish_login(self._login_step.state)
                return await self._async_finish()
            except AbortFlow:
                raise
            except ProviderLoginPending:
                errors["base"] = "not_scanned"
            except ProviderAuthError:
                errors["base"] = "auth_failed"
            except ProviderError:
                errors["base"] = "cannot_connect"
        schema = vol.Schema(
            {
                vol.Optional("QR"): selector.QrCodeSelector(
                    selector.QrCodeSelectorConfig(
                        data=self._login_step.qr_payload or "",
                        scale=5,
                        error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE,
                    )
                )
            }
        )
        return self.async_show_form(step_id="scan", data_schema=schema, errors=errors)

    async def _async_finish(self) -> ConfigFlowResult:
        assert self._provider is not None
        data = {
            CONF_PROVIDER: self._provider.key,
            CONF_PROVIDER_DATA: self._provider.to_entry_data(),
        }
        title = self._provider.name.split(" (")[0]
        username = getattr(self._provider, "username", "") or getattr(self._provider, "_email", "")
        if username:
            title = f"{title} ({username})"
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(self._reauth_entry, data={**self._reauth_entry.data, **data})
        await self.async_set_unique_id(self._provider.account_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=data)

    # ------------------------------------------------------------------ reauth

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Re-authenticate."""
        self._reauth_entry = self._get_reauth_entry()
        self._provider_key = entry_data[CONF_PROVIDER]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm, then log in again."""
        if user_input is not None:
            return await self.async_step_login()
        assert self._provider_key is not None
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"provider": get_provider_class(self._provider_key).name},
        )


class TuyaEcoOptionsFlow(OptionsFlow):
    """Options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_POLL_INTERVAL, default=options.get(OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5, max=600, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    OPT_CLOUD_REFRESH_HOURS, default=options.get(OPT_CLOUD_REFRESH_HOURS, DEFAULT_CLOUD_REFRESH_HOURS)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=168, step=1, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    OPT_CLOUD_FALLBACK, default=options.get(OPT_CLOUD_FALLBACK, DEFAULT_CLOUD_FALLBACK)
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
