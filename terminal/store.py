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
-- 10.3 hand audits of classifications: one row per item checked
CREATE TABLE IF NOT EXISTS classification_audits (
    audited_on     DATE,
    kind           VARCHAR,      -- channel | video
    item_id        VARCHAR,
    sector         VARCHAR,      -- sector of the code the model assigned
    assigned_code  VARCHAR,
    correct_code   VARCHAR,
    correct        BOOLEAN,
    auditor        VARCHAR,
    PRIMARY KEY (audited_on, kind, item_id)
);
-- cross-platform (9.x): every channel belongs to one platform; ids from other platforms carry a prefix (tt:, ig:)
ALTER TABLE channels ADD COLUMN IF NOT EXISTS platform VARCHAR DEFAULT 'youtube';
-- 6.6-6.7, 8.2-8.5 outcome series the terminal's signals are tested against (search interest, sales, revenue)
CREATE TABLE IF NOT EXISTS outcome_series (
    series_id      VARCHAR,      -- e.g. trends:03.07:US
    source         VARCHAR,      -- google_trends | csv | synthetic
    code           VARCHAR,      -- the CTNCS topic it belongs to
    label          VARCHAR,
    d              DATE,         -- week start
    value          DOUBLE,
    PRIMARY KEY (series_id, d)
);
-- 6.3 sampled comments and whether each asks where to buy, price, link or availability
CREATE TABLE IF NOT EXISTS video_comments (
    comment_id     VARCHAR PRIMARY KEY,
    video_id       VARCHAR,
    published_at   TIMESTAMP,
    text           VARCHAR,
    purchase_intent BOOLEAN,
    tagged_by      VARCHAR,      -- rules | model
    pulled_on      DATE
);
-- 6.2 brands, products and companies named in a video's title, description or tags
CREATE TABLE IF NOT EXISTS video_entities (
    video_id       VARCHAR,
    entity         VARCHAR,
    entity_type    VARCHAR,      -- brand | company | category
    matched_in     VARCHAR,
    PRIMARY KEY (video_id, entity)
);
-- 2.11 packaging format (draft CTNCS v3 codes in config/ctncs/packaging.csv)
CREATE TABLE IF NOT EXISTS video_packaging (
    video_id       VARCHAR PRIMARY KEY,
    packaging_id   VARCHAR,
    packaging_label VARCHAR,
    tagged_by      VARCHAR
);
-- 10.4 outside estimates of a topic's total views, for basket coverage
CREATE TABLE IF NOT EXISTS outside_estimates (
    code           VARCHAR,
    period_start   DATE,
    period_end     DATE,
    total_views    DOUBLE,
    source         VARCHAR,
    PRIMARY KEY (code, period_start)
);
-- orchestration: one row per stage per run
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id         VARCHAR,
    job            VARCHAR,
    stage          VARCHAR,
    started_at     TIMESTAMP,
    finished_at    TIMESTAMP,
    status         VARCHAR,      -- ok | warn | failed | skipped
    attempts       INTEGER,
    detail         VARCHAR
);
ALTER TABLE videos ADD COLUMN IF NOT EXISTS description VARCHAR;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS tags VARCHAR;
"""


def connect(path: str | None = None) -> duckdb.DuckDBPyConnection:
    path = path or os.environ.get("TERMINAL_DB", "data/terminal.duckdb")
    if path.startswith("md:"):
        # MotherDuck: connect to the account, create the database on the first run, then use it
        if not (os.environ.get("MOTHERDUCK_TOKEN") or os.environ.get("motherduck_token")):
            raise SystemExit("Set MOTHERDUCK_TOKEN (motherduck.com > Settings > Access tokens) to use " + path)
        name = path[3:] or "content_terminal"
        con = duckdb.connect("md:")
        con.execute(f'CREATE DATABASE IF NOT EXISTS "{name}"')
        con.execute(f'USE "{name}"')
    else:
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        con = duckdb.connect(path)
    con.execute(SCHEMA)
    return con
