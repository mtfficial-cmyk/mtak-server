# lora_detector.py
"""
LoRa hardware detector — scans PC serial ports for known LoRa/Meshtastic
chip signatures (Silicon Labs CP210x, CH340, FTDI, etc.).

Called by the /api/lora/status endpoint so the ATAK Android app can show
a LoRa connectivity badge without requiring BLE on the phone.
"""

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# USB chip descriptions that appear on common LoRa boards:
# Heltec, TTGO T-Beam, RAK4631, WisBlock, generic ESP32 LoRa devboards
_LORA_KEYWORDS = [
    "silicon labs",
    "cp210",
    "ch340",
    "ch341",
    "ftdi",
    "usb serial",
    "usb-serial",
    "t3s3",
    "rak",
    "wisblock",
    "heltec",
    "ttgo",
    "meshtastic",
    "esp32",
]


def _scan_ports() -> Optional[str]:
    """Return the first serial port device path that looks like a LoRa board,
    or None if none found."""
    try:
        import serial.tools.list_ports
        for port in serial.tools.list_ports.comports():
            desc = ((port.description or "") + " " + (port.manufacturer or "")).lower()
            if any(kw in desc for kw in _LORA_KEYWORDS):
                return port.device
    except Exception as e:
        logger.warning(f"[LoRa] Serial port scan failed: {e}")
    return None


def get_status() -> Dict[str, Any]:
    """
    Returns a dict suitable for JSON serialisation:
      {
        "connected": true | false,
        "port":      "COM3" | "/dev/ttyUSB0" | null,
        "node_name": "LoRa Node" | null
      }
    Called on every GET /api/lora/status request (fast — no I/O beyond
    listing serial ports).
    """
    port = _scan_ports()
    if port:
        logger.debug(f"[LoRa] Hardware detected on {port}")
        return {"connected": True,  "port": port,  "node_name": "LoRa Node"}
    return     {"connected": False, "port": None,  "node_name": None}
