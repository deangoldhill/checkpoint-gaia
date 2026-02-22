import logging
from datetime import timedelta
import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import Platform

from .const import (
    DOMAIN, CONF_HOST, CONF_USERNAME, 
    CONF_PASSWORD, CONF_PORT, CONF_VERIFY_SSL,
    UPDATE_INTERVAL_SECONDS
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = CheckPointCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

class CheckPointCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.base_url = f"https://{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}/web_api/v1.8"
        self.verify_ssl = entry.data[CONF_VERIFY_SSL]

    async def _async_update_data(self):
        try:
            connector = aiohttp.TCPConnector(ssl=self.verify_ssl)
            async with aiohttp.ClientSession(connector=connector) as session:
                # 1. Login
                login_payload = {
                    "user": self.entry.data[CONF_USERNAME],
                    "password": self.entry.data[CONF_PASSWORD]
                }
                async with session.post(f"{self.base_url}/login", json=login_payload) as resp:
                    resp.raise_for_status()
                    login_data = await resp.json()
                    sid = login_data.get("sid")
                
                headers = {"X-chkp-sid": sid, "Content-Type": "application/json"}
                data = {}

                # 2. Fetch API Data
                endpoints = [
                    "show-diagnostics",
                    "show-serial-number",
                    "show-version",
                    "show-asset",
                    "show-hostname"
                ]

                for endpoint in endpoints:
                    async with session.post(f"{self.base_url}/{endpoint}", headers=headers, json={}) as resp:
                        if resp.status == 200:
                            data[endpoint] = await resp.json()

                # 3. Logout
                await session.post(f"{self.base_url}/logout", headers=headers, json={})

                return self._parse_data(data)
                
        except Exception as e:
            raise UpdateFailed(f"Error communicating with CheckPoint API: {e}")

    def _parse_data(self, raw_data):
        parsed = {}
        
        # Parse show-diagnostics
        diag = raw_data.get("show-diagnostics", {})
        if diag:
            # Memory %
            mem = diag.get("memory", {})
            total_mem = mem.get("total", 1) # Prevent div by 0
            free_mem = mem.get("free", 0)
            parsed["memory_usage"] = round(((total_mem - free_mem) / total_mem) * 100, 2)
            
            # CPU Total %
            cpu = diag.get("cpu", {}).get("total", {})
            parsed["cpu_usage"] = cpu.get("usage", 0)

            # Disk Usage (/ and /var/log)
            for disk in diag.get("disk", []):
                mount = disk.get("mount-point")
                if mount == "/":
                    parsed["disk_root_used"] = disk.get("used-percentage", 0)
                elif mount == "/var/log":
                    parsed["disk_var_log_used"] = disk.get("used-percentage", 0)

        # Parse show-serial-number
        parsed["serial_number"] = raw_data.get("show-serial-number", {}).get("serial-number", "Unknown")

        # Parse show-version
        parsed["product_version"] = raw_data.get("show-version", {}).get("product-version", "Unknown")

        # Parse show-asset
        sys_asset = raw_data.get("show-asset", {}).get("system", {})
        parsed["cpu_cores"] = sys_asset.get("cpu-cores", "Unknown")

        # Parse show-hostname
        parsed["hostname"] = raw_data.get("show-hostname", {}).get("hostname", "Unknown")

        return parsed
