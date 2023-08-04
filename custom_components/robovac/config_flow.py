# Copyright 2022 Brendan McCluskey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Config flow for Eufy Robovac integration."""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Optional

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_DESCRIPTION,
    CONF_ID,
    CONF_IP_ADDRESS,
    CONF_LOCATION,
    CONF_MAC,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import selector

from .const import CONF_PHONE_CODE, CONF_VACS, DOMAIN
from .eufywebapi import EufyLogon
from .tuyawebapi import TuyaAPISession

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)


def get_eufy_vacuums(self):
    """Login to Eufy and get the vacuum details"""

    eufy_session = EufyLogon(self["username"], self["password"])
    response = eufy_session.get_user_info()
    if response.status_code != 200:
        raise CannotConnect

    user_response = response.json()
    if user_response["res_code"] != 1:
        raise InvalidAuth

    self[CONF_CLIENT_ID] = user_response["user_info"]["id"]
    self[CONF_PHONE_CODE] = user_response["user_info"]["phone_code"]

    tuya_client = TuyaAPISession(
        username="eh-" + self[CONF_CLIENT_ID], country_code=self[CONF_PHONE_CODE]
    )
    allvacs = {}
    for home in tuya_client.list_homes():
        for device in tuya_client.list_devices(home["groupId"]):
            vac_details = {
                CONF_ACCESS_TOKEN: device["localKey"],
                CONF_LOCATION: home["groupId"],
            }

            allvacs[device["devId"]] = vac_details

    response = eufy_session.get_device_info(
        user_response["user_info"]["request_host"],
        user_response["user_info"]["id"],
        user_response["access_token"],
    )

    device_response = response.json()

    items = device_response["items"]
    for item in items:
        if (
            item["device"]["product"]["appliance"] == "Cleaning"
            and item["device"]["id"] in allvacs
        ):
            vac_details = {
                CONF_ID: item["device"]["id"],
                CONF_MODEL: item["device"]["product"]["product_code"],
                CONF_NAME: item["device"]["alias_name"],
                CONF_DESCRIPTION: item["device"]["name"],
                CONF_MAC: item["device"]["wifi"]["mac"],
                CONF_IP_ADDRESS: "",
            }
            allvacs[item["device"]["id"]].update(vac_details)

    self[CONF_VACS] = allvacs

    return response


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    await hass.async_add_executor_job(get_eufy_vacuums, data)
    return data


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eufy Robovac."""

    data: Optional[dict[str, Any]]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=USER_SCHEMA)
        errors = {}
        try:
            unique_id = user_input[CONF_USERNAME]
            valid_data = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(title=unique_id, data=valid_data)
        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handles options flow for the component."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] = None
    ) -> dict[str, Any]:
        """Manage the options for the custom component."""
        errors: dict[str, str] = {}

        vac_names = []
        vacuums = self.config_entry.data[CONF_VACS]
        for item in vacuums:
            item_settings = vacuums[item]
            vac_names.append(item_settings["name"])
        if user_input is not None:
            for item in vacuums:
                item_settings = vacuums[item]
                if item_settings["name"] == user_input["vacuum"]:
                    item_settings[CONF_IP_ADDRESS] = user_input[CONF_IP_ADDRESS]
            updated_repos = deepcopy(self.config_entry.data[CONF_VACS])

            if not errors:
                # Value of data will be set on the options property of our config_entry
                # instance.
                return self.async_create_entry(
                    title="",
                    data={CONF_VACS: updated_repos},
                )

        options_schema = vol.Schema(
            {
                vol.Optional("vacuum", default=1): selector(
                    {
                        "select": {
                            "options": vac_names,
                        }
                    }
                ),
                vol.Optional(CONF_IP_ADDRESS): cv.string,
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=options_schema, errors=errors
        )
