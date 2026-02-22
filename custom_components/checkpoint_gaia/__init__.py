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

                # 2. Fetch Simple API Data
                simple_endpoints = [
                    "show-serial-number",
                    "show-version",
                    "show-asset",
                    "show-hostname"
                ]

                for endpoint in simple_endpoints:
                    async with session.post(f"{self.base_url}/{endpoint}", headers=headers, json={}) as resp:
                        if resp.status == 200:
                            data[endpoint] = await resp.json()

                # 3. Fetch Diagnostics Data
                diag_topics = ["cpu", "memory", "disk"]
                for topic in diag_topics:
                    payload = {
                        "category": "os",
                        "topic": topic
                    }
                    async with session.post(f"{self.base_url}/show-diagnostics", headers=headers, json=payload) as resp:
                        if resp.status == 200:
                            data[f"diag_{topic}"] = await resp.json()

                # 4. Logout
                await session.post(f"{self.base_url}/logout", headers=headers, json={})

                return self._parse_data(data)
                
        except Exception as e:
            raise UpdateFailed(f"Error communicating with CheckPoint Gaia API: {e}")

    def _parse_data(self, raw_data):
        parsed = {}

        def find_key(obj, target_key):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if str(k).lower() == target_key.lower():
                        return v
            return None

        # --- DIAGNOSTICS: CPU ---
        diag_cpu = raw_data.get("diag_cpu", {})
        cpu_objects = diag_cpu.get("objects", [])
        if cpu_objects:
            total_idle = 0
            valid_cores = 0
            for core in cpu_objects:
                idle = find_key(core, "idle")
                if idle is not None:
                    total_idle += float(idle)
                    valid_cores += 1
            if valid_cores > 0:
                avg_idle = total_idle / valid_cores
                parsed["cpu_usage"] = round(100.0 - avg_idle, 2)

        # --- DIAGNOSTICS: MEMORY ---
        diag_mem = raw_data.get("diag_memory", {})
        mem_objects = diag_mem.get("objects", [])
        if mem_objects:
            mem_obj = mem_objects[0]
            total_mem = float(find_key(mem_obj, "total") or 0)
            free_mem = float(find_key(mem_obj, "free") or 0)
            if total_mem > 0:
                parsed["memory_usage"] = round(((total_mem - free_mem) / total_mem) * 100, 2)

        # --- DIAGNOSTICS: DISK ---
        diag_disk = raw_data.get("diag_disk", {})
        disk_objects = diag_disk.get("objects", [])
        for disk in disk_objects:
            partition = disk.get("partition")
            used = disk.get("used")
            total = disk.get("total")
            
            if partition and used is not None and total is not None:
                try:
                    used_pct = (float(used) / float(total)) * 100
                    if partition == "/":
                        parsed["disk_root_used"] = round(used_pct, 2)
                    elif partition == "/var/log":
                        parsed["disk_var_log_used"] = round(used_pct, 2)
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

        # --- SIMPLE ASSETS & SYSTEM INFO ---
        sn_data = raw_data.get("show-serial-number", {})
        parsed["serial_number"] = find_key(sn_data, "serial-number") or "Unknown"

        ver_data = raw_data.get("show-version", {})
        parsed["product_version"] = find_key(ver_data, "product-version") or "Unknown"

        host_data = raw_data.get("show-hostname", {})
        parsed["hostname"] = find_key(host_data, "hostname") or find_key(host_data, "name") or "Unknown"

        # --- HARDWARE ASSETS (Platform, Cores, Model, Frequency) ---
        asset_data = raw_data.get("show-asset", {})
        system_assets = asset_data.get("system", [])
        
        # Set defaults
        parsed["cpu_cores"] = "Unknown"
        parsed["platform"] = "Unknown"
        parsed["cpu_model"] = "Unknown"
        parsed["cpu_frequency"] = None # None handles unknown states for measurement classes better in HA
        parsed["cpu_hyperthreading"] = "Unknown"

        if isinstance(system_assets, list):
            for item in system_assets:
                if isinstance(item, dict):
                    key = item.get("key")
                    val = item.get("value", "Unknown")
                    
                    if key == "Number of Cores":
                        parsed["cpu_cores"] = val
                    elif key == "Platform":
                        parsed["platform"] = val
                    elif key == "CPU Model":
                        parsed["cpu_model"] = val
                    elif key == "CPU Frequency":
                        try:
                            # Strip out "MHz" or any extra spaces so HA only gets the pure number
                            clean_val = str(val).lower().replace("mhz", "").strip()
                            parsed["cpu_frequency"] = round(float(clean_val), 2)
                        except (ValueError, TypeError):
                            pass
                    elif key == "CPU Hyperthreading":
                        parsed["cpu_hyperthreading"] = val

        return parsed
