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
        self.base_url = f"https://{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}/gaia_api/v1.8"
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
                    
                    # Safely get SID whether login_data is a dict or a wrapped list
                    if isinstance(login_data, dict):
                        sid = login_data.get("sid")
                    else:
                        sid = login_data[0].get("sid")
                
                headers = {"X-chkp-sid": sid, "Content-Type": "application/json"}
                data = {}

                # 2. Fetch API Data
                endpoints = [
                    "show-diagnostics",
                    "show-serial-number",
                    "show-version",
                    "show-asset",
                    "show-hostname",
                    "show-connections"
                ]

                for endpoint in endpoints:
                    async with session.post(f"{self.base_url}/{endpoint}", headers=headers, json={}) as resp:
                        if resp.status == 200:
                            data[endpoint] = await resp.json()

                # 3. Logout
                await session.post(f"{self.base_url}/logout", headers=headers, json={})

                return self._parse_data(data)
                
        except Exception as e:
            raise UpdateFailed(f"Error communicating with CheckPoint Gaia API: {e}")

    def _parse_data(self, raw_data):
        parsed = {}
        
        # Helper function to safely extract keys regardless of dict or list structures
        def safe_get(obj, key, default=None):
            if default is None:
                default = {}
            if isinstance(obj, dict):
                return obj.get(key, default)
            if isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict) and key in item:
                        return item.get(key, default)
            return default

        # Parse show-diagnostics
        diag = safe_get(raw_data, "show-diagnostics")
        if diag:
            # Memory %
            mem = safe_get(diag, "memory")
            total_mem = float(safe_get(mem, "total", 1)) # Prevent div by 0
            free_mem = float(safe_get(mem, "free", 0))
            if total_mem > 0:
                parsed["memory_usage"] = round(((total_mem - free_mem) / total_mem) * 100, 2)
            
            # CPU Total %
            cpu = safe_get(diag, "cpu")
            cpu_total = safe_get(cpu, "total")
            parsed["cpu_usage"] = float(safe_get(cpu_total, "usage", 0))

            # Disk Usage (/ and /var/log)
            disk_data = safe_get(diag, "disk", [])
            if isinstance(disk_data, dict):
                disk_data = [disk_data] # Normalize to list if API returned a single dict
                
            if isinstance(disk_data, list):
                for disk in disk_data:
                    if isinstance(disk, dict):
                        mount = disk.get("mount-point")
                        if mount == "/":
                            parsed["disk_root_used"] = float(disk.get("used-percentage", 0))
                        elif mount == "/var/log":
                            parsed["disk_var_log_used"] = float(disk.get("used-percentage", 0))

        # Parse show-serial-number
        sn_data = safe_get(raw_data, "show-serial-number")
        parsed["serial_number"] = safe_get(sn_data, "serial-number", "Unknown")

        # Parse show-version
        ver_data = safe_get(raw_data, "show-version")
        parsed["product_version"] = safe_get(ver_data, "product-version", "Unknown")

        # Parse show-asset
        asset_data = safe_get(raw_data, "show-asset")
        sys_asset = safe_get(asset_data, "system")
        parsed["cpu_cores"] = safe_get(sys_asset, "cpu-cores", "Unknown")

        # Parse show-hostname
        host_data = safe_get(raw_data, "show-hostname")
        parsed["hostname"] = safe_get(host_data, "hostname", "Unknown")

        # Parse show-connections
        conn_data = safe_get(raw_data, "show-connections")
        parsed["concurrent_connections"] = safe_get(
            conn_data, "total", safe_get(
                conn_data, "connections", safe_get(
                    conn_data, "concurrent-connections", 0
                )
            )
        )

        return parsed
