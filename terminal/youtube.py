"""Daily pulls from the official YouTube Data API v3.

Quota (default 10,000 units/day): channels.list, playlistItems.list and videos.list
cost 1 unit per call. Channels and videos go 50 per call; recent uploads (up to 50) are one
call per channel. So ~3,000 channels at 50 uploads each costs roughly 60 + 3,000 + 3,000 =
~6,000 units a day. Check the current quota and the API terms (data retention and display
rules) in Google Cloud before scaling up.
"""
import datetime as dt
import os
import re
import requests

API = "https://www.googleapis.com/youtube/v3"


def _key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise SystemExit("Set YOUTUBE_API_KEY (Google Cloud > APIs > YouTube Data API v3 > Credentials).")
    return key


def _get(path: str, **params) -> dict:
    r = requests.get(f"{API}/{path}", params={**params, "key": _key()}, timeout=30)
    r.raise_for_status()
    return r.json()


def _chunks(items, n=50):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _duration_s(iso: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def resolve_handle(handle: str) -> str | None:
    """@handle -> channel id (1 unit)."""
    items = _get("channels", part="id", forHandle=handle.lstrip("@")).get("items", [])
    return items[0]["id"] if items else None


def search_recent_videos(query: str, published_after: dt.datetime, region: str | None = None,
                         language: str | None = None, page_token: str | None = None) -> tuple[list[str], str | None]:
    """Recent videos for a query, most viewed first (100 units per page). Returns (channel ids, next page token)."""
    params = {"part": "snippet", "type": "video", "q": query, "order": "viewCount", "maxResults": 50,
              "publishedAfter": published_after.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if region:
        params["regionCode"] = region
    if language:
        params["relevanceLanguage"] = language
    if page_token:
        params["pageToken"] = page_token
    data = _get("search", **params)
    return [i["snippet"]["channelId"] for i in data.get("items", [])], data.get("nextPageToken")


def channel_stats(channel_ids: list[str]) -> dict:
    """Title, subscribers, video count for each channel (1 unit per 50), without writing to the database."""
    out = {}
    for batch in _chunks(channel_ids):
        for c in _get("channels", part="snippet,statistics", id=",".join(batch), maxResults=50).get("items", []):
            st = c.get("statistics", {})
            out[c["id"]] = {"title": c["snippet"].get("title"), "subscribers": int(st.get("subscriberCount", 0)),
                            "videos": int(st.get("videoCount", 0)), "hidden": st.get("hiddenSubscriberCount", False)}
    return out


def pull_channels(con, channel_ids: list[str], today: dt.date) -> int:
    n = 0
    for batch in _chunks(channel_ids):
        data = _get("channels", part="snippet,statistics,contentDetails", id=",".join(batch), maxResults=50)
        for c in data.get("items", []):
            sn, st = c["snippet"], c.get("statistics", {})
            con.execute(
                "INSERT OR REPLACE INTO channels (channel_id, title, description, country, published_at, uploads_playlist) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [c["id"], sn.get("title"), sn.get("description"), sn.get("country"), sn.get("publishedAt"),
                 c["contentDetails"]["relatedPlaylists"]["uploads"]],
            )
            con.execute(
                "INSERT OR REPLACE INTO channel_snapshots VALUES (?, ?, ?, ?, ?)",
                [today, c["id"], int(st.get("subscriberCount", 0)), int(st.get("viewCount", 0)),
                 int(st.get("videoCount", 0))],
            )
            n += 1
    return n


def pull_recent_videos(con, today: dt.date, per_channel: int = 50) -> int:
    """Latest uploads per channel, then current stats for each video."""
    playlists = con.execute("SELECT channel_id, uploads_playlist FROM channels").fetchall()
    video_ids = []
    for channel_id, playlist in playlists:
        try:
            data = _get("playlistItems", part="contentDetails", playlistId=playlist, maxResults=per_channel)
        except requests.HTTPError:
            continue  # channel deleted or uploads hidden
        video_ids += [i["contentDetails"]["videoId"] for i in data.get("items", [])]

    n = 0
    for batch in _chunks(video_ids):
        data = _get("videos", part="snippet,statistics,contentDetails", id=",".join(batch), maxResults=50)
        for v in data.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            con.execute(
                "INSERT OR REPLACE INTO videos (video_id, channel_id, title, published_at, duration_s, description, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [v["id"], sn["channelId"], sn.get("title"), sn.get("publishedAt"),
                 _duration_s(v["contentDetails"].get("duration")), (sn.get("description") or "")[:1000],
                 ", ".join(sn.get("tags", [])[:15]) or None],
            )
            con.execute(
                "INSERT OR REPLACE INTO video_snapshots VALUES (?, ?, ?, ?, ?)",
                [today, v["id"], int(st.get("viewCount", 0)), int(st.get("likeCount", 0)),
                 int(st.get("commentCount", 0))],
            )
            n += 1
    return n
