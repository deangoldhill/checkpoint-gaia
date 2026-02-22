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
        
        # Recursive helper to find a key no matter how deeply nested it is in the JSON
        def deep_find(obj, key):
            if isinstance(obj, dict):
                if key in obj:
                    return obj[key]
                for v in obj.values():
                    res = deep_find(v, key)
                    if res is not None:
                        return res
            elif isinstance(obj, list):
                for item in obj:
                    res = deep_find(item, key)
                    if res is not None:
                        return res
            return None

        # 1. Parse show-diagnostics
        diag = raw_data.get("show-diagnostics", {})
        if diag:
            # Memory %
            mem = deep_find(diag, "memory") or {}
            total_mem = float(mem.get("total", 1) if mem.get("total") is not None else 1)
            free_mem = float(mem.get("free", 0) if mem.get("free") is not None else 0)
            if total_mem > 1:
                parsed["memory_usage"] = round(((total_mem - free_mem) / total_mem) * 100, 2)
            
            # CPU Total %
            cpu = deep_find(diag, "cpu") or {}
            cpu_total = deep_find(cpu, "total") or {}
            cpu_usage = cpu_total.get("usage")
            if cpu_usage is not None:
                parsed["cpu_usage"] = float(cpu_usage)

            # Disk Usage (/ and /var/log)
            disk = deep_find(diag, "disk")
            if isinstance(disk, dict):
                disk = [disk]
            if isinstance(disk, list):
                for d in disk:
                    if isinstance(d, dict):
                        mount = d.get("mount-point")
                        if mount == "/":
                            parsed["disk_root_used"] = float(d.get("used-percentage", 0))
                        elif mount == "/var/log":
                            parsed["disk_var_log_used"] = float(d.get("used-percentage", 0))

        # 2. Parse show-serial-number
        sn_data = raw_data.get("show-serial-number", {})
        parsed["serial_number"] = deep_find(sn_data, "serial-number") or "Unknown"

        # 3. Parse show-version
        ver_data = raw_data.get("show-version", {})
        parsed["product_version"] = deep_find(ver_data, "product-version") or "Unknown"

        # 4. Parse show-asset
        asset_data = raw_data.get("show-asset", {})
        parsed["cpu_cores"] = deep_find(asset_data, "cpu-cores") or "Unknown"

        # 5. Parse show-hostname
        host_data = raw_data.get("show-hostname", {})
        # Sometimes Gaia API keys it as "hostname", sometimes as "name"
        parsed["hostname"] = deep_find(host_data, "hostname") or deep_find(host_data, "name") or "Unknown"

        # 6. Parse show-connections
        conn_data = raw_data.get("show-connections", {})
        conn_total = None
        
        # Check all common variations Check Point uses for connection counts
        for k in ["total", "connections", "concurrent-connections", "active-connections", "count"]:
            conn_total = deep_find(conn_data, k)
            if conn_total is not None:
                break
                
        # Fallback: if it returns an actual list of connections instead of a count
        if conn_total is None:
            conn_list = deep_find(conn_data, "objects")
            if isinstance(conn_list, list):
                conn_total = len(conn_list)

        parsed["concurrent_connections"] = float(conn_total) if conn_total is not None else 0

        return parsed
