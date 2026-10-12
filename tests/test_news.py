"""News parsing and ranking, offline."""
import datetime as dt

from terminal import news, store

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Platform raises creator revenue share</title><link>https://example.com/a</link>
<pubDate>Fri, 09 Oct 2026 19:19:31 +0000</pubDate><description>Big change.</description></item>
<item><title>Old roundup</title><link>https://example.com/b</link><pubDate>Mon, 07 Sep 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>Atom item</title><link href="https://example.com/c"/><updated>2026-10-10T12:00:00Z</updated></entry></feed>"""


def test_parse_rss_and_atom():
    items = news.parse_feed(RSS, "Test")
    assert [i["url"] for i in items] == ["https://example.com/a", "https://example.com/b"]
    assert items[0]["published_at"] == dt.datetime(2026, 10, 9, 19, 19, 31)
    atom = news.parse_feed(ATOM, "Atom")
    assert atom[0]["url"] == "https://example.com/c" and atom[0]["published_at"] == dt.datetime(2026, 10, 10, 12)


def test_ranking_prefers_recent_high_impact_and_matches_tracked_creators():
    con = store.connect(":memory:")
    news.connect(con)
    con.execute("INSERT INTO channels (channel_id, title) VALUES ('UCx', 'Dhar Mann')")
    now = dt.datetime.now()
    rows = [("u1", "HIGH", now - dt.timedelta(days=1), None), ("u2", "HIGH", now - dt.timedelta(days=30), None),
            ("u3", "LOW", now - dt.timedelta(days=1), "Dhar Mann, Someone Else")]
    for url, impact, ts, creators in rows:
        con.execute("INSERT INTO news (url, source, title, published_at, category, platform, impact, sentiment, creators) "
                    "VALUES (?, 's', 't', ?, 'DEAL', 'ALL', ?, 'NEUTRAL', ?)", [url, ts, impact, creators])
    ranked = con.execute("SELECT url, tracked_channel_ids FROM news_ranked ORDER BY score DESC").fetchall()
    assert [r[0] for r in ranked] == ["u1", "u3", "u2"]
    assert dict(ranked)["u3"] == "UCx"
