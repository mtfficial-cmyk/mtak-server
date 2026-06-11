-- Integrated Schema for ATAK_MI_Server

-- 0. USER ACCOUNTS (from ATAK-CIV-Package logic)
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL DEFAULT 'user', -- 'admin' | 'user'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 1. GPS LOCATIONS
CREATE TABLE IF NOT EXISTS locations (
    id            BIGSERIAL PRIMARY KEY,
    device_id     TEXT        NOT NULL,
    username      TEXT        NOT NULL,
    room          TEXT        NOT NULL DEFAULT 'lobby',
    latitude      DOUBLE PRECISION NOT NULL,
    longitude     DOUBLE PRECISION NOT NULL,
    accuracy_m    REAL,
    altitude_m    DOUBLE PRECISION,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_to_t2  BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_locations_device ON locations(device_id);
CREATE INDEX IF NOT EXISTS idx_locations_room   ON locations(room);
CREATE INDEX IF NOT EXISTS idx_locations_ts     ON locations(ts DESC);

-- 2. CHAT MESSAGES
CREATE TABLE IF NOT EXISTS messages (
    id             BIGSERIAL PRIMARY KEY,
    device_id      TEXT        NOT NULL,
    username       TEXT        NOT NULL,
    room           TEXT        NOT NULL DEFAULT 'lobby',
    message_text   TEXT        NOT NULL,
    ts             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_to_t2   BOOLEAN     NOT NULL DEFAULT FALSE,
    device_msg_id  TEXT        -- Android UUID; NULL for non-Android senders
);
CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room);
CREATE INDEX IF NOT EXISTS idx_messages_ts   ON messages(ts DESC);
-- Partial unique index: prevents double-insert of the same Android message UUID.
-- NULL values are excluded so non-Android rows never conflict.
CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_device_msg_id
    ON messages(device_msg_id)
    WHERE device_msg_id IS NOT NULL;

-- 3. MAP MARKERS
CREATE TABLE IF NOT EXISTS markers (
    id            BIGSERIAL PRIMARY KEY,
    marker_uid    TEXT        NOT NULL,
    device_id     TEXT        NOT NULL,
    username      TEXT        NOT NULL,
    room          TEXT        NOT NULL DEFAULT 'lobby',
    marker_type   TEXT        NOT NULL DEFAULT 'circle',
    color         TEXT        NOT NULL DEFAULT '#00D4FF',
    latitude      DOUBLE PRECISION NOT NULL,
    longitude     DOUBLE PRECISION NOT NULL,
    label         TEXT,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_to_t2  BOOLEAN     NOT NULL DEFAULT FALSE,
    UNIQUE(marker_uid)
);
CREATE INDEX IF NOT EXISTS idx_markers_room ON markers(room);

-- 4. SAFETY ZONES
CREATE TABLE IF NOT EXISTS zones (
    id            BIGSERIAL PRIMARY KEY,
    zone_uid      TEXT        NOT NULL,
    device_id     TEXT        NOT NULL,
    username      TEXT        NOT NULL,
    room          TEXT        NOT NULL DEFAULT 'lobby',
    status        TEXT        NOT NULL DEFAULT 'safe',
    latitude      DOUBLE PRECISION NOT NULL,
    longitude     DOUBLE PRECISION NOT NULL,
    radius_m      DOUBLE PRECISION NOT NULL,
    description   TEXT,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_to_t2  BOOLEAN     NOT NULL DEFAULT FALSE,
    UNIQUE(zone_uid)
);

-- 5. ROUTES
CREATE TABLE IF NOT EXISTS routes (
    id            BIGSERIAL PRIMARY KEY,
    route_uid     TEXT        NOT NULL,
    device_id     TEXT        NOT NULL,
    username      TEXT        NOT NULL,
    room          TEXT        NOT NULL DEFAULT 'lobby',
    route_name    TEXT,
    waypoints     JSONB       NOT NULL,
    total_dist_m  DOUBLE PRECISION,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_to_t2  BOOLEAN     NOT NULL DEFAULT FALSE,
    UNIQUE(route_uid)
);

-- 6. MEASUREMENT LINES
CREATE TABLE IF NOT EXISTS measurements (
    id            BIGSERIAL PRIMARY KEY,
    measure_uid   TEXT        NOT NULL,
    device_id     TEXT        NOT NULL,
    username      TEXT        NOT NULL,
    start_lat     DOUBLE PRECISION NOT NULL,
    start_lon     DOUBLE PRECISION NOT NULL,
    end_lat       DOUBLE PRECISION NOT NULL,
    end_lon       DOUBLE PRECISION NOT NULL,
    distance_m    DOUBLE PRECISION NOT NULL,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_to_t2  BOOLEAN     NOT NULL DEFAULT FALSE,
    UNIQUE(measure_uid)
);

-- 7. MEDIA FILES
CREATE TABLE IF NOT EXISTS media (
    id            BIGSERIAL PRIMARY KEY,
    device_id     TEXT        NOT NULL,
    username      TEXT        NOT NULL,
    room          TEXT        NOT NULL DEFAULT 'lobby',
    media_type    TEXT        NOT NULL,
    bucket_name   TEXT        NOT NULL,
    object_key    TEXT        NOT NULL,
    file_size_b   BIGINT,
    mime_type     TEXT,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_to_t2  BOOLEAN     NOT NULL DEFAULT FALSE
);

-- 8. ALERTS
CREATE TABLE IF NOT EXISTS alerts (
    id            BIGSERIAL PRIMARY KEY,
    device_id     TEXT        NOT NULL,
    username      TEXT        NOT NULL,
    room          TEXT        NOT NULL DEFAULT 'lobby',
    alert_type    TEXT        NOT NULL,
    alert_text    TEXT,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_to_t2  BOOLEAN     NOT NULL DEFAULT FALSE
);

-- 9. ADS-B SNAPSHOTS
CREATE TABLE IF NOT EXISTS adsb_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    device_id     TEXT        NOT NULL,
    icao_hex      TEXT        NOT NULL,
    callsign      TEXT,
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    altitude_ft   INTEGER,
    speed_kts     INTEGER,
    heading_deg   SMALLINT,
    squawk        TEXT,
    is_military   BOOLEAN     NOT NULL DEFAULT FALSE,
    is_emergency  BOOLEAN     NOT NULL DEFAULT FALSE,
    api_source    TEXT        NOT NULL DEFAULT 'airplanes.live',
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_to_t2  BOOLEAN     NOT NULL DEFAULT FALSE
);

-- 10a. DRAWINGS (freehand shapes, polygons, annotations from Drawing Tools)
CREATE TABLE IF NOT EXISTS drawings (
    id            BIGSERIAL PRIMARY KEY,
    drawing_uid   TEXT        NOT NULL,
    device_id     TEXT        NOT NULL,
    username      TEXT        NOT NULL,
    room          TEXT        NOT NULL DEFAULT 'lobby',
    drawing_type  TEXT        NOT NULL DEFAULT 'freehand', -- 'freehand' | 'polygon' | 'line' | 'circle'
    color         TEXT        NOT NULL DEFAULT '#FF0000',
    label         TEXT,
    geojson       JSONB       NOT NULL,                   -- GeoJSON Feature payload
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    synced_to_t2  BOOLEAN     NOT NULL DEFAULT FALSE,
    UNIQUE(drawing_uid)
);
CREATE INDEX IF NOT EXISTS idx_drawings_room ON drawings(room);
CREATE INDEX IF NOT EXISTS idx_drawings_ts   ON drawings(ts DESC);

-- 10b. DEVICE SETTINGS (per-user toggle state — Night Vision, Grid, ADSB, Track History)
CREATE TABLE IF NOT EXISTS device_settings (
    username      TEXT        NOT NULL PRIMARY KEY,
    settings      JSONB       NOT NULL DEFAULT '{}',      -- arbitrary key/value toggle map
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 10. PRESENCE
CREATE TABLE IF NOT EXISTS presence (
    id          BIGSERIAL   PRIMARY KEY,
    device_id   TEXT        NOT NULL DEFAULT '',
    username    TEXT        NOT NULL UNIQUE,
    room        TEXT        NOT NULL DEFAULT '',
    status      TEXT        NOT NULL DEFAULT 'online',
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_presence_last_seen ON presence(last_seen DESC);
