"""DuckDB storage. One file locally, or MotherDuck ("md:content_terminal") in the cloud."""
import os
import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id     VARCHAR PRIMARY KEY,
    title          VARCHAR,
    description    VARCHAR,
    country        VARCHAR,
    published_at   TIMESTAMP,
    uploads_playlist VARCHAR,
    added_at       TIMESTAMP DEFAULT now()
);
CREATE TABLE IF NOT EXISTS channel_snapshots (
    snapshot_date  DATE,
    channel_id     VARCHAR,
    subscribers    BIGINT,
    views          BIGINT,
    video_count    BIGINT,
    PRIMARY KEY (snapshot_date, channel_id)
);
CREATE TABLE IF NOT EXISTS videos (
    video_id       VARCHAR PRIMARY KEY,
    channel_id     VARCHAR,
    title          VARCHAR,
    published_at   TIMESTAMP,
    duration_s     INTEGER
);
CREATE TABLE IF NOT EXISTS video_snapshots (
    snapshot_date  DATE,
    video_id       VARCHAR,
    views          BIGINT,
    likes          BIGINT,
    comments       BIGINT,
    PRIMARY KEY (snapshot_date, video_id)
);
CREATE TABLE IF NOT EXISTS classifications (
    channel_id     VARCHAR PRIMARY KEY,
    ctncs_code     VARCHAR,      -- deepest code assigned, e.g. 08.02.03
    sector         VARCHAR,      -- level 1
    category       VARCHAR,      -- level 2
    niche          VARCHAR,      -- level 3
    confidence     DOUBLE,
    rationale      VARCHAR,
    model          VARCHAR,
    reviewed       BOOLEAN DEFAULT false,   -- true once a person has audited it
    classified_at  TIMESTAMP DEFAULT now()
);
-- Video-level classification: what each piece of content is about, independent of its channel.
CREATE TABLE IF NOT EXISTS video_classifications (
    video_id       VARCHAR PRIMARY KEY,
    topic_code     VARCHAR,      -- deepest CTNCS topic assigned (up to level 4, e.g. 02.05.010.019 Woodworking)
    sector         VARCHAR,      -- level 1
    category       VARCHAR,      -- level 2
    niche          VARCHAR,      -- level 3
    subniche       VARCHAR,      -- level 4
    format_id      VARCHAR,      -- CTNCS v2.0 format code, e.g. 14 = DIY (independent of topic)
    format_label   VARCHAR,
    confidence     DOUBLE,
    model          VARCHAR,
    reviewed       BOOLEAN DEFAULT false,
    classified_at  TIMESTAMP DEFAULT now()
);
ALTER TABLE videos ADD COLUMN IF NOT EXISTS description VARCHAR;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS tags VARCHAR;
"""


def connect(path: str | None = None) -> duckdb.DuckDBPyConnection:
    path = path or os.environ.get("TERMINAL_DB", "data/terminal.duckdb")
    con = duckdb.connect(path)
    con.execute(SCHEMA)
    return con
