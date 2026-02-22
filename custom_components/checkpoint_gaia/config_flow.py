import voluptuous as vol
from homeassistant import config_entries
from .const import (
    DOMAIN, CONF_HOST, CONF_USERNAME, 
    CONF_PASSWORD, CONF_PORT, CONF_VERIFY_SSL,
    CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
)

class CheckPointGaiaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_HOST], data=user_input)

        data_schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_PORT, default=443): int,
            vol.Optional(CONF_VERIFY_SSL, default=False): bool,
            vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): int,
        })
        return self.async_show_form(step_id="user", data_schema=data_schema)
