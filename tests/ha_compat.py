"""Lightweight Home Assistant compatibility shim for offline unit testing."""

from __future__ import annotations

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


class SensorEntity(Entity):
    """Mock SensorEntity."""


class BinarySensorEntity(Entity):
    """Mock BinarySensorEntity."""


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


class ConfigEntry:
    """Mock ConfigEntry."""

    @classmethod
    def __class_getitem__(cls, item: Any) -> Any:
        return cls

    def __init__(
        self,
        entry_id: str = "test_entry",
        data: dict[str, Any] | None = None,
        unique_id: str | None = None,
        title: str = "Weheat cloud",
    ) -> None:
        self.entry_id = entry_id
        self.data = data or {}
        self.unique_id = unique_id
        self.title = title
        self.runtime_data: Any = []


class ConfigEntries:
    """Mock ConfigEntries manager."""

    def __init__(self) -> None:
        self.forwarded_setups: list[tuple[Any, list[Platform]]] = []
        self.unloaded_platforms: list[tuple[Any, list[Platform]]] = []

    async def async_forward_entry_setups(
        self, entry: ConfigEntry, platforms: list[Platform]
    ) -> None:
        self.forwarded_setups.append((entry, platforms))

    async def async_unload_platforms(
        self, entry: ConfigEntry, platforms: list[Platform]
    ) -> bool:
        self.unloaded_platforms.append((entry, platforms))
        return True


class HomeAssistant:
    """Mock HomeAssistant core instance."""

    def __init__(self) -> None:
        import logging

        self.logger = logging.getLogger("homeassistant")
        self.config_entries = ConfigEntries()


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
    if "homeassistant" in sys.modules and hasattr(
        sys.modules["homeassistant"], "const"
    ):
        return

    ha = types.ModuleType("homeassistant")
    ha_const = types.ModuleType("homeassistant.const")
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

    ha_config_entries = types.ModuleType("homeassistant.config_entries")
    ha_config_entries.ConfigEntry = ConfigEntry
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
