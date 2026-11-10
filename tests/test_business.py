"""Business signals: revenue streams and sponsors read from video descriptions."""
import datetime as dt

from terminal import business, store

T = dt.datetime(2026, 11, 1, 15)


def _video(con, vid, ch, desc, flag=None, when=T):
    con.execute("INSERT INTO videos (video_id, channel_id, title, published_at, duration_s, description, paid_placement) "
                "VALUES (?, ?, 't', ?, 600, ?, ?)", [vid, ch, when, desc, flag])


def test_brand_names_and_links():
    assert business.brand_of("drinklmnt.com") == "LMNT" and business.brand_of("monarchmoney.yt.link") == "Monarch Money"
    assert business.brand_of("shop.anker.com") == "Anker" and business.brand_of("join.whoop.com") == "WHOOP"
    assert business.links("x https://www.instagra cut https://drinklmnt.com/a") == [("drinklmnt.com", "/a")]
    assert business.stream_of("youtube.com", "/@me/join") == "Membership" and business.stream_of("shop.myshopify.com") == "Own site or store"


def test_streams_sponsors_and_own_store():
    con = store.connect(":memory:")
    # creator A: sponsor wording + code, affiliate, own store in every video
    for i in range(6):
        desc = "Merch https://astore.com\nGear https://amzn.to/x"
        if i < 3:
            desc += "\nThis video is sponsored by LMNT, use code A: https://drinklmnt.com/a"
        _video(con, f"a{i}", "UCa", desc)
    # creator B: a brand link with no sponsor wording, also used by C, so it counts as a sponsor; Patreon; YouTube's flag
    _video(con, "b1", "UCb", "https://helixsleep.com/b https://patreon.com/b")
    _video(con, "b2", "UCb", "nothing here", flag=True)
    _video(con, "c1", "UCc", "https://helixsleep.com/c https://instagram.com/c https://pubmed.ncbi.nlm.nih.gov/1")
    # an old video falls outside the 90-day window
    _video(con, "c0", "UCc", "https://patreon.com/c", when=dt.datetime(2025, 1, 1))
    msg = business.build(con, dt.date(2026, 11, 10))
    assert msg.startswith("10 videos read")
    cb = {r[0]: r[1:] for r in con.execute("SELECT channel_id, videos_90d, sponsored_videos_90d, top_sponsors, streams FROM creator_business").fetchall()}
    a = cb["UCa"]
    assert a[0] == 6 and a[1] == 3 and a[2] == ["LMNT"]
    assert {"Sponsorships", "Affiliate", "Own site or store"} <= set(a[3])
    b = cb["UCb"]
    assert b[1] == 2 and b[2] == ["Helix Sleep"] and "Membership" in b[3]   # the flag alone counts as sponsored
    assert cb["UCc"][0] == 1 and "Membership" not in cb["UCc"][3]          # the Patreon link is older than 90 days
    sm = {r[0]: r[1] for r in con.execute("SELECT brand, creators FROM sponsor_map").fetchall()}
    assert sm == {"Helix Sleep": 2, "LMNT": 1}                              # social and research links never become sponsors
