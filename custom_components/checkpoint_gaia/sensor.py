from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    sensors = [
        CheckPointSensor(coordinator, "memory_usage", "Memory Usage", PERCENTAGE, "mdi:memory", True),
        CheckPointSensor(coordinator, "cpu_usage", "CPU Usage", PERCENTAGE, "mdi:cpu-64-bit", True),
        CheckPointSensor(coordinator, "disk_root_used", "Disk Root Used", PERCENTAGE, "mdi:harddisk", True),
        CheckPointSensor(coordinator, "disk_var_log_used", "Disk /var/log Used", PERCENTAGE, "mdi:folder", True),
        CheckPointSensor(coordinator, "serial_number", "Serial Number", None, "mdi:barcode", False),
        CheckPointSensor(coordinator, "product_version", "Product Version", None, "mdi:information", False),
        CheckPointSensor(coordinator, "cpu_cores", "CPU Cores", None, "mdi:chip", False),
        CheckPointSensor(coordinator, "hostname", "Hostname", None, "mdi:network", False),
    ]
    
    async_add_entities(sensors)

class CheckPointSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, key, name, unit, icon, is_state_class):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = f"CheckPoint {coordinator.data.get('hostname', 'Firewall')} {name}"
        self._attr_unique_id = f"cp_{coordinator.entry.data['host']}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        if is_state_class:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.entry.entry_id)},
            "name": f"CheckPoint Gaia ({coordinator.data.get('hostname', 'Unknown')})",
            "manufacturer": "Check Point",
            "model": "Gaia Firewall",
            "sw_version": coordinator.data.get("product_version"),
        }

    @property
    def native_value(self):
        return self.coordinator.data.get(self._key)
