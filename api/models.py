from pydantic import BaseModel, Field
from typing import Optional, List, Any

class UserLogin(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str
    password: str
    role: Optional[str] = "user"

class LocationIn(BaseModel):
    device_id:  str
    username:   str
    room:       str = "lobby"
    latitude:   float
    longitude:  float
    accuracy_m: Optional[float] = None
    altitude_m: Optional[float] = None
    ts:         Optional[str]   = None

class MessageIn(BaseModel):
    device_id:    str
    username:     str
    room:         str = "lobby"
    message_text: str
    ts:           Optional[str] = None

class MarkerIn(BaseModel):
    device_id:   str
    username:    str
    room:        str = "lobby"
    marker_uid:  str
    marker_type: str = "circle"
    color:       str = "#00D4FF"
    latitude:    float
    longitude:   float
    label:       Optional[str] = None
    ts:          Optional[str] = None

class ZoneIn(BaseModel):
    device_id:   str
    username:    str
    room:        str = "lobby"
    zone_uid:    str
    status:      str = "safe"
    latitude:    float
    longitude:   float
    radius_m:    float
    description: Optional[str] = None
    ts:          Optional[str] = None

class PresenceIn(BaseModel):
    device_id: str = ""
    username:  str
    room:      str = "lobby"
    status:    str = "online"

class AdsbAircraft(BaseModel):
    icao_hex:     str
    callsign:     Optional[str] = None
    lat:          Optional[float] = None
    lon:          Optional[float] = None
    altitude_ft:  Optional[int] = None
    speed_kts:    Optional[int] = None
    heading_deg:  Optional[int] = None
    squawk:       Optional[str] = None
    is_military:  bool = False
    is_emergency: bool = False

class AdsbBatchIn(BaseModel):
    device_id:  str
    api_source: str = "airplanes.live"
    aircraft:   List[AdsbAircraft]

class AlertIn(BaseModel):
    device_id:  str
    username:   str
    room:       str = "lobby"
    alert_type: str = "DEFAULT"
    alert_text: str
    ts:         Optional[str] = None

class RouteIn(BaseModel):
    device_id:    str
    username:     str
    room:         str = "lobby"
    route_uid:    str
    route_name:   Optional[str] = None
    waypoints:    Any                  # JSON array of {lat, lon} objects
    total_dist_m: Optional[float] = None
    ts:           Optional[str] = None

class MeasurementIn(BaseModel):
    device_id:   str
    username:    str
    room:        str = "lobby"
    measure_uid: str
    start_lat:   float
    start_lon:   float
    end_lat:     float
    end_lon:     float
    distance_m:  float
    ts:          Optional[str] = None

class DrawingIn(BaseModel):
    device_id:    str
    username:     str
    room:         str = "lobby"
    drawing_uid:  str
    drawing_type: str = "freehand"
    color:        str = "#FF0000"
    label:        Optional[str] = None
    geojson:      Any                  # GeoJSON Feature object
    ts:           Optional[str] = None
