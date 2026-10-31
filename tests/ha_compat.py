"""Lightweight Home Assistant compatibility shim for offline unit testing."""

from __future__ import annotations

import datetime
import inspect
import sys
import types
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any


class SensorDeviceClass(StrEnum):
    POWER = "power"
    TEMPERATURE = "temperature"
    ENUM = "enum"
    VOLUME_FLOW_RATE = "volume_flow_rate"
    TIMESTAMP = "timestamp"
    ENERGY = "energy"


class SensorStateClass(StrEnum):
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class BinarySensorDeviceClass(StrEnum):
    RUNNING = "running"


class EntityCategory(StrEnum):
    DIAGNOSTIC = "diagnostic"
    CONFIG = "config"


class UnitOfPower(StrEnum):
    WATT = "W"
    KILO_WATT = "kW"


class UnitOfEnergy(StrEnum):
    KILO_WATT_HOUR = "kWh"


class UnitOfTemperature(StrEnum):
    CELSIUS = "°C"


class UnitOfVolumeFlowRate(StrEnum):
    CUBIC_METERS_PER_HOUR = "m³/h"


class Platform(StrEnum):
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    SELECT = "select"
    SWITCH = "switch"
    TIME = "time"
    BUTTON = "button"
    NUMBER = "number"


PERCENTAGE = "%"
REVOLUTIONS_PER_MINUTE = "rpm"
CONF_ACCESS_TOKEN = "access_token"
CONF_TOKEN = "token"
SOURCE_REAUTH = "reauth"


@dataclass(frozen=True, kw_only=True)
class EntityDescription:
    key: str
    translation_key: str | None = None
    device_class: Any = None
    entity_category: Any = None
    entity_registry_enabled_default: bool = True
    name: str | None = None


@dataclass(frozen=True, kw_only=True)
class SensorEntityDescription(EntityDescription):
    state_class: Any = None
    native_unit_of_measurement: str | None = None
    suggested_display_precision: int | None = None
    options: list[str] | None = None


@dataclass(frozen=True, kw_only=True)
class BinarySensorEntityDescription(EntityDescription):
    pass


class Entity:
    """Mock Home Assistant Entity base."""

    _attr_has_entity_name: bool = False
    _attr_unique_id: str | None = None
    _attr_device_info: Any = None

    @property
    def unique_id(self) -> str | None:
        return self._attr_unique_id

    @property
    def device_info(self) -> Any:
        return self._attr_device_info

    def async_write_ha_state(self) -> None:
        """Mock writing entity state to state machine."""
        pass


class SensorEntity(Entity):
    """Mock SensorEntity."""


class BinarySensorEntity(Entity):
    """Mock BinarySensorEntity."""


@dataclass(frozen=True, kw_only=True)
class SelectEntityDescription(EntityDescription):
    options: list[str] | None = None


class SelectEntity(Entity):
    """Mock SelectEntity."""

    _attr_current_option: str | None = None
    _attr_options: list[str] = []

    @property
    def current_option(self) -> str | None:
        return self._attr_current_option

    @property
    def options(self) -> list[str]:
        return self._attr_options

    async def async_select_option(self, option: str) -> None:
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True)
class SwitchEntityDescription(EntityDescription):
    pass


class SwitchEntity(Entity):
    """Mock SwitchEntity."""

    _attr_is_on: bool | None = None

    @property
    def is_on(self) -> bool | None:
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        raise NotImplementedError

    async def async_turn_off(self, **kwargs: Any) -> None:
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True)
class TimeEntityDescription(EntityDescription):
    pass


class TimeEntity(Entity):
    """Mock TimeEntity."""

    _attr_native_value: datetime.time | None = None

    @property
    def native_value(self) -> datetime.time | None:
        return self._attr_native_value

    async def async_set_value(self, value: datetime.time) -> None:
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True)
class ButtonEntityDescription(EntityDescription):
    pass


class ButtonEntity(Entity):
    """Mock ButtonEntity."""

    async def async_press(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True)
class NumberEntityDescription(EntityDescription):
    native_max_value: float = 100.0
    native_min_value: float = 0.0
    native_step: float = 1.0


class NumberEntity(Entity):
    """Mock NumberEntity."""

    _attr_native_value: float | None = None

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        raise NotImplementedError


class CoordinatorEntity(Entity):
    """Mock CoordinatorEntity."""

    @classmethod
    def __class_getitem__(cls, item: Any) -> Any:
        return cls

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator


class DataUpdateCoordinator:
    """Mock DataUpdateCoordinator."""

    @classmethod
    def __class_getitem__(cls, item: Any) -> Any:
        return cls

    def __init__(
        self,
        hass: Any,
        logger: Any,
        name: str,
        config_entry: Any = None,
        update_interval: timedelta | None = None,
    ) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.config_entry = config_entry
        self.update_interval = update_interval
        self.data: Any = None

    async def _async_update_data(self) -> Any:
        raise NotImplementedError

    async def async_config_entry_first_refresh(self) -> None:
        self.data = await self._async_update_data()

    async def async_request_refresh(self) -> None:
        self.data = await self._async_update_data()


class HomeAssistantError(Exception):
    """Base Home Assistant Exception."""


class ConfigEntryAuthFailed(HomeAssistantError):
    """Auth failed exception."""


class ConfigEntryNotReady(HomeAssistantError):
    """Entry not ready exception."""


class UpdateFailed(HomeAssistantError):
    """Update failed exception."""


class DeviceInfo(dict):
    """Mock DeviceInfo matching Home Assistant device registry."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        for k, v in kwargs.items():
            setattr(self, k, v)


@dataclass
class ClientCredential:
    """Mock ClientCredential."""

    client_id: str
    client_secret: str
    name: str | None = None


class ConfigEntry:
    """Mock ConfigEntry."""

    @classmethod
    def __class_getitem__(cls, item: Any) -> Any:
        return cls

    def __init__(
        self,
        entry_id: str = "test_entry",
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        unique_id: str | None = None,
        title: str = "Weheat cloud",
        runtime_data: Any = None,
    ) -> None:
        self.entry_id = entry_id
        self.data = data or {}
        self.options = options or {}
        self.unique_id = unique_id
        self.title = title
        self.runtime_data: Any = runtime_data if runtime_data is not None else []
        self.update_listeners: list[Any] = []
        self._on_unload: list[Any] = []

    @property
    def _update_listeners(self) -> list[Any]:
        return self.update_listeners

    @property
    def _unload_listeners(self) -> list[Any]:
        return self._on_unload

    def add_update_listener(self, listener: Any) -> Any:
        """Register an update listener callback."""
        self.update_listeners.append(listener)

        def remove_listener() -> None:
            if listener in self.update_listeners:
                self.update_listeners.remove(listener)

        return remove_listener

    def async_on_unload(self, func: Any) -> Any:
        """Register a callback or unsub function for entry unload."""
        self._on_unload.append(func)
        return func


@dataclass
class ServiceCall:
    """Mock ServiceCall matching Home Assistant core."""

    domain: str
    service: str
    data: dict[str, Any]
    context: Any = None


class ServiceRegistry:
    """Mock ServiceRegistry managing registered actions/services."""

    def __init__(self) -> None:
        self._services: dict[str, dict[str, Any]] = {}

    def has_service(self, domain: str, service: str) -> bool:
        """Return True if service is registered."""
        return domain in self._services and service in self._services[domain]

    def async_register(
        self,
        domain: str,
        service: str,
        service_func: Any,
        schema: Any = None,
    ) -> None:
        """Register a service handler."""
        if domain not in self._services:
            self._services[domain] = {}
        self._services[domain][service] = {
            "func": service_func,
            "schema": schema,
        }

    def async_remove(self, domain: str, service: str) -> None:
        """Remove a registered service."""
        if domain in self._services:
            self._services[domain].pop(service, None)

    async def async_call(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        target: Any = None,
        blocking: bool = True,
    ) -> Any:
        """Execute a service call."""
        if not self.has_service(domain, service):
            raise HomeAssistantError(f"Service {domain}.{service} not registered")
        item = self._services[domain][service]
        func = item["func"]
        data = dict(service_data or {})
        if target:
            data["target"] = target
        call = ServiceCall(domain=domain, service=service, data=data)
        if inspect.iscoroutinefunction(func):
            return await func(call)
        res = func(call)
        if inspect.isawaitable(res):
            return await res
        return res


class ConfigEntries:
    """Mock ConfigEntries manager."""

    def __init__(self) -> None:
        self.forwarded_setups: list[tuple[Any, list[Platform]]] = []
        self.unloaded_platforms: list[tuple[Any, list[Platform]]] = []
        self._entries: dict[str, list[Any]] = {}
        self.reloaded_entries: list[str] = []

    def async_entries(self, domain: str) -> list[Any]:
        return self._entries.get(domain, [])

    def async_add(self, entry: Any) -> None:
        domain = getattr(entry, "domain", "weheat")
        self._entries.setdefault(domain, []).append(entry)

    async def async_forward_entry_setups(
        self, entry: ConfigEntry, platforms: list[Platform]
    ) -> None:
        self.forwarded_setups.append((entry, platforms))
        domain = getattr(entry, "domain", "weheat")
        if entry not in self._entries.get(domain, []):
            self._entries.setdefault(domain, []).append(entry)

    async def async_unload_platforms(
        self, entry: ConfigEntry, platforms: list[Platform]
    ) -> bool:
        self.unloaded_platforms.append((entry, platforms))
        return True

    async def async_reload(self, entry_id: str) -> bool:
        """Mock reloading a config entry."""
        self.reloaded_entries.append(entry_id)
        return True


class HomeAssistant:
    """Mock HomeAssistant core instance."""

    def __init__(self) -> None:
        import logging

        self.logger = logging.getLogger("homeassistant")
        self.config_entries = ConfigEntries()
        self.services = ServiceRegistry()


class LocalOAuth2ImplementationWithPkce:
    """Mock LocalOAuth2ImplementationWithPkce."""

    def __init__(
        self,
        hass: HomeAssistant,
        auth_domain: str,
        client_id: str,
        authorize_url: str,
        token_url: str,
        client_secret: str,
    ) -> None:
        self.hass = hass
        self.auth_domain = auth_domain
        self.client_id = client_id
        self.authorize_url = authorize_url
        self.token_url = token_url
        self.client_secret = client_secret


class OAuth2Session:
    """Mock OAuth2Session."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, implementation: Any
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.implementation = implementation
        self.token: dict[str, Any] = entry.data.get(CONF_TOKEN, {})

    async def async_ensure_token_valid(self) -> None:
        """Validate token or raise ClientResponseError."""
        pass


async def async_get_config_entry_implementation(
    hass: HomeAssistant, entry: ConfigEntry
) -> LocalOAuth2ImplementationWithPkce:
    return LocalOAuth2ImplementationWithPkce(
        hass,
        "weheat",
        "mock_client_id",
        "https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/auth/",
        "https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/token/",
        "mock_secret",
    )


_OAUTH_IMPLEMENTATIONS: dict[str, dict[str, Any]] = {}


def async_register_implementation(
    hass: HomeAssistant, domain: str, implementation: Any
) -> None:
    """Register an OAuth2 implementation."""
    name = getattr(implementation, "name", getattr(implementation, "client_id", domain))
    _OAUTH_IMPLEMENTATIONS.setdefault(domain, {})[name] = implementation


async def async_get_implementations(hass: HomeAssistant, domain: str) -> dict[str, Any]:
    """Get all registered OAuth2 implementations for a domain."""
    return dict(_OAUTH_IMPLEMENTATIONS.get(domain, {}))


async def async_import_client_credential(
    hass: HomeAssistant,
    auth_domain: str,
    credential: ClientCredential,
    name: str = "Imported",
) -> None:
    """Mock importing client credentials into application credentials storage."""
    impl = LocalOAuth2ImplementationWithPkce(
        hass=hass,
        auth_domain=auth_domain,
        client_id=credential.client_id,
        authorize_url="https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/auth/",
        token_url="https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/token/",
        client_secret=credential.client_secret,
    )
    impl.name = name or getattr(credential, "name", None) or credential.client_id
    async_register_implementation(hass, auth_domain, impl)


def async_get_clientsession(hass: Any) -> Any:
    return getattr(hass, "session", None)


class FlowAbortException(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class AbstractOAuth2FlowHandler:
    """Mock AbstractOAuth2FlowHandler."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__()
        cls.flow_domain = kwargs.get("domain")

    def __init__(self) -> None:
        self.hass: HomeAssistant = HomeAssistant()
        self.source: str = "user"
        self.unique_id: str | None = None
        self._reauth_entry: ConfigEntry | None = None
        self.configured_unique_ids: set[str] = set()

    async def async_set_unique_id(self, unique_id: str) -> None:
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self) -> None:
        if self.unique_id in self.configured_unique_ids:
            raise FlowAbortException("already_configured")

    def _abort_if_unique_id_mismatch(self, reason: str = "wrong_account") -> None:
        if self._reauth_entry and self.unique_id != self._reauth_entry.unique_id:
            raise FlowAbortException(reason)

    def _get_reauth_entry(self) -> ConfigEntry:
        if self._reauth_entry is None:
            raise RuntimeError("No reauth entry")
        return self._reauth_entry

    def async_create_entry(self, title: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data}

    def async_abort(self, reason: str) -> dict[str, Any]:
        return {"type": "abort", "reason": reason}

    def async_update_reload_and_abort(
        self, entry: ConfigEntry, data_updates: dict[str, Any]
    ) -> dict[str, Any]:
        entry.data.update(data_updates)
        return {"type": "abort", "reason": "reauth_successful", "entry": entry}

    def async_show_form(
        self,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"type": "external", "step_id": "auth"}


class OptionsFlow:
    """Mock OptionsFlow."""

    def __init__(self) -> None:
        self.hass: HomeAssistant = HomeAssistant()

    def async_create_entry(
        self, title: str = "", data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data or {}}

    def async_show_form(
        self,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }


def async_redact_data(data: dict[str, Any], to_redact: set[str]) -> dict[str, Any]:
    """Redact sensitive fields from data dictionary."""
    if not isinstance(data, dict):
        return data
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        if key in to_redact:
            redacted[key] = "**REDACTED**"
        elif isinstance(value, dict):
            redacted[key] = async_redact_data(value, to_redact)
        elif isinstance(value, list):
            redacted[key] = [
                async_redact_data(v, to_redact) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            redacted[key] = value
    return redacted


def install_ha_stubs() -> None:
    """Install stub modules into sys.modules if homeassistant is not present."""
    if "homeassistant" in sys.modules and "homeassistant.const" in sys.modules:
        return

    ha = types.ModuleType("homeassistant")
    ha_const = types.ModuleType("homeassistant.const")
    ha.const = ha_const
    ha_const.PERCENTAGE = PERCENTAGE
    ha_const.REVOLUTIONS_PER_MINUTE = REVOLUTIONS_PER_MINUTE
    ha_const.CONF_ACCESS_TOKEN = CONF_ACCESS_TOKEN
    ha_const.CONF_TOKEN = CONF_TOKEN
    ha_const.EntityCategory = EntityCategory
    ha_const.UnitOfPower = UnitOfPower
    ha_const.UnitOfEnergy = UnitOfEnergy
    ha_const.UnitOfTemperature = UnitOfTemperature
    ha_const.UnitOfVolumeFlowRate = UnitOfVolumeFlowRate
    ha_const.Platform = Platform

    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = HomeAssistant
    ha_core.ServiceCall = ServiceCall

    ha_config_entries = types.ModuleType("homeassistant.config_entries")
    ha_config_entries.ConfigEntry = ConfigEntry
    ha_config_entries.OptionsFlow = OptionsFlow
    ha_config_entries.SOURCE_REAUTH = SOURCE_REAUTH
    ha_config_entries.ConfigFlowResult = Any

    ha_exceptions = types.ModuleType("homeassistant.exceptions")
    ha_exceptions.HomeAssistantError = HomeAssistantError
    ha_exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    ha_exceptions.ConfigEntryNotReady = ConfigEntryNotReady

    ha_helpers = types.ModuleType("homeassistant.helpers")

    ha_helpers_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
    ha_helpers_aiohttp.async_get_clientsession = async_get_clientsession

    ha_helpers_oauth = types.ModuleType(
        "homeassistant.helpers.config_entry_oauth2_flow"
    )
    ha_helpers_oauth.AbstractOAuth2FlowHandler = AbstractOAuth2FlowHandler
    ha_helpers_oauth.LocalOAuth2ImplementationWithPkce = (
        LocalOAuth2ImplementationWithPkce
    )
    ha_helpers_oauth.OAuth2Session = OAuth2Session
    ha_helpers_oauth.async_get_config_entry_implementation = (
        async_get_config_entry_implementation
    )
    ha_helpers_oauth.async_get_implementations = async_get_implementations
    ha_helpers_oauth.async_register_implementation = async_register_implementation

    ha_helpers_devreg = types.ModuleType("homeassistant.helpers.device_registry")
    ha_helpers_devreg.DeviceInfo = DeviceInfo

    ha_helpers_entity = types.ModuleType("homeassistant.helpers.entity")
    ha_helpers_entity.Entity = Entity
    ha_helpers_entity.EntityDescription = EntityDescription

    ha_helpers_ep = types.ModuleType("homeassistant.helpers.entity_platform")
    ha_helpers_ep.AddConfigEntryEntitiesCallback = Any

    ha_helpers_uc = types.ModuleType("homeassistant.helpers.update_coordinator")
    ha_helpers_uc.CoordinatorEntity = CoordinatorEntity
    ha_helpers_uc.DataUpdateCoordinator = DataUpdateCoordinator
    ha_helpers_uc.UpdateFailed = UpdateFailed

    ha_helpers_typing = types.ModuleType("homeassistant.helpers.typing")
    ha_helpers_typing.StateType = Any

    ha_components = types.ModuleType("homeassistant.components")

    ha_components_appcred = types.ModuleType(
        "homeassistant.components.application_credentials"
    )
    ha_components_appcred.ClientCredential = ClientCredential
    ha_components_appcred.async_import_client_credential = (
        async_import_client_credential
    )

    ha_components_sensor = types.ModuleType("homeassistant.components.sensor")
    ha_components_sensor.SensorEntity = SensorEntity
    ha_components_sensor.SensorDeviceClass = SensorDeviceClass
    ha_components_sensor.SensorStateClass = SensorStateClass
    ha_components_sensor.SensorEntityDescription = SensorEntityDescription

    ha_components_bs = types.ModuleType("homeassistant.components.binary_sensor")
    ha_components_bs.BinarySensorEntity = BinarySensorEntity
    ha_components_bs.BinarySensorDeviceClass = BinarySensorDeviceClass
    ha_components_bs.BinarySensorEntityDescription = BinarySensorEntityDescription

    ha_components_diag = types.ModuleType("homeassistant.components.diagnostics")
    ha_components_diag.async_redact_data = async_redact_data

    ha_components_select = types.ModuleType("homeassistant.components.select")
    ha_components_select.SelectEntity = SelectEntity
    ha_components_select.SelectEntityDescription = SelectEntityDescription

    ha_components_switch = types.ModuleType("homeassistant.components.switch")
    ha_components_switch.SwitchEntity = SwitchEntity
    ha_components_switch.SwitchEntityDescription = SwitchEntityDescription

    ha_components_time = types.ModuleType("homeassistant.components.time")
    ha_components_time.TimeEntity = TimeEntity
    ha_components_time.TimeEntityDescription = TimeEntityDescription

    ha_components_button = types.ModuleType("homeassistant.components.button")
    ha_components_button.ButtonEntity = ButtonEntity
    ha_components_button.ButtonEntityDescription = ButtonEntityDescription

    ha_components_number = types.ModuleType("homeassistant.components.number")
    ha_components_number.NumberEntity = NumberEntity
    ha_components_number.NumberEntityDescription = NumberEntityDescription

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.const"] = ha_const
    sys.modules["homeassistant.core"] = ha_core
    sys.modules["homeassistant.config_entries"] = ha_config_entries
    sys.modules["homeassistant.exceptions"] = ha_exceptions
    sys.modules["homeassistant.helpers"] = ha_helpers
    sys.modules["homeassistant.helpers.aiohttp_client"] = ha_helpers_aiohttp
    sys.modules["homeassistant.helpers.config_entry_oauth2_flow"] = ha_helpers_oauth
    sys.modules["homeassistant.helpers.device_registry"] = ha_helpers_devreg
    sys.modules["homeassistant.helpers.entity"] = ha_helpers_entity
    sys.modules["homeassistant.helpers.entity_platform"] = ha_helpers_ep
    sys.modules["homeassistant.helpers.update_coordinator"] = ha_helpers_uc
    sys.modules["homeassistant.helpers.typing"] = ha_helpers_typing
    sys.modules["homeassistant.components"] = ha_components
    sys.modules["homeassistant.components.application_credentials"] = (
        ha_components_appcred
    )
    sys.modules["homeassistant.components.sensor"] = ha_components_sensor
    sys.modules["homeassistant.components.binary_sensor"] = ha_components_bs
    sys.modules["homeassistant.components.diagnostics"] = ha_components_diag
    sys.modules["homeassistant.components.select"] = ha_components_select
    sys.modules["homeassistant.components.switch"] = ha_components_switch
    sys.modules["homeassistant.components.time"] = ha_components_time
    sys.modules["homeassistant.components.button"] = ha_components_button
    sys.modules["homeassistant.components.number"] = ha_components_number
