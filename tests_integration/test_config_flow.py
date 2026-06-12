"""Tests for the Gabb Wireless config flow."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.gabb_wireless.api import GabbAuthError, GabbConnectionError
from custom_components.gabb_wireless.const import DOMAIN

USER_INPUT = {"username": "Parent@Example.com", "password": "hunter2"}


async def test_user_flow_success(hass: HomeAssistant) -> None:
    """A valid login creates a config entry keyed by the lowercased username."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with (
        patch(
            "custom_components.gabb_wireless.config_flow.GabbApiClient.async_login",
            return_value=None,
        ),
        patch(
            "custom_components.gabb_wireless.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Parent@Example.com"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == "parent@example.com"


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    """A rejected login shows the invalid_auth error and re-renders the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.gabb_wireless.config_flow.GabbApiClient.async_login",
        side_effect=GabbAuthError,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """A network failure shows the cannot_connect error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.gabb_wireless.config_flow.GabbApiClient.async_login",
        side_effect=GabbConnectionError,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(hass: HomeAssistant) -> None:
    """The same account (case-insensitive) cannot be added twice."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    MockConfigEntry(
        domain=DOMAIN,
        unique_id="parent@example.com",
        data=USER_INPUT,
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.gabb_wireless.config_flow.GabbApiClient.async_login",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
