"""Platform monetization rates: the board from the registry, the decisions log, RPM by niche and the ask views."""
import datetime as dt

from terminal import ask, rates, store


def test_board_reads_last_and_next_change():
    b = {r["id"]: r for r in rates.board(today=dt.date(2026, 11, 9))}
    assert b["yt_long"]["level"] == 55 and b["yt_long"]["disclosed"] == "Yes"
    # a dollar rate moves in percent of itself; a share moves in points
    assert round(b["roblox_devex"]["last_change"], 1) == 8.6 and b["roblox_devex"]["last_change_unit"] == "%"
    assert b["patreon"]["last_change"] == -2 and b["patreon"]["last_change_unit"] == "pts"
    assert b["fn_items"]["next_date"] == "2027-02-01" and b["fn_items"]["next_new"] == 50
    assert b["tiktok_rewards"]["level"] is None and b["tiktok_rewards"]["disclosed"] == "No"
    assert b["kick_subs"]["last_kind"] == "new" and b["spotify_spp"]["last_kind"] == "terms"
    # every row and change carries a source; nothing is stale on the day it was verified
    assert all(r["source"].startswith("http") and not r["stale"] for r in b.values())
    assert all(c["source"].startswith("http") for c in rates.changes())
    # a scheduled change does not count as the last change until its date passes
    later = {r["id"]: r for r in rates.board(today=dt.date(2027, 3, 1))}
    assert later["fn_items"]["last_date"] == "2027-02-01" and later["fn_items"]["next_date"] is None
    assert all(r["stale"] for r in later.values())


def test_rpm_by_niche_and_ask_views():
    con = store.connect(":memory:")
    con.execute("INSERT INTO channels (channel_id, title) VALUES ('UCa', 'A')")
    con.execute("CREATE TABLE channel_niche AS SELECT 'UCa' AS channel_id, '03.07.013' AS niche_code, '03' AS sector")
    hits = [{"id": "UCa", "monetization": {"rpmEstimationFrom": 2, "rpmEstimationTo": 6}},
            {"id": "UCb", "monetization": {"rpmEstimationFrom": 4, "rpmEstimationTo": 10}},
            {"id": "UCc", "monetization": {}}]
    msg = rates.load_rpm(con, dt.date(2026, 11, 9), label=lambda c: "Running", search=lambda q: hits)
    assert msg.startswith("1 of 1 niches")
    r = rates.latest_rpm(con)[0]
    assert (r["channels"], r["tracked_matches"], r["rpm_low"], r["rpm_mid"], r["rpm_high"]) == (2, 1, 3, 5.5, 8)
    # monthly: a second run inside the window spends no credits
    assert rates.load_rpm(con, dt.date(2026, 11, 20), label=lambda c: "x", search=lambda q: 1 / 0).startswith("fresh")
    board = {x["id"]: x for x in rates.board(rpm=rates.latest_rpm(con))}
    assert board["yt_long"]["market_rpm"] == 5.5
    made = ask.build(con)
    assert {"ask_platform_rates", "ask_rate_changes", "ask_niche_rpm"} <= set(made)
    assert con.execute("SELECT creator_share FROM ask_platform_rates WHERE platform = 'Kick'").fetchone()[0] == 95


def test_one_failed_niche_keeps_the_rest():
    import datetime as dt
    from terminal import rates, store
    con = store.connect(":memory:")
    con.execute("CREATE TABLE channel_niche AS SELECT * FROM (VALUES ('UCa', '03.07', '03'), ('UCb', '08.25', '08')) t(channel_id, niche_code, sector)")
    hit = [{"id": "UCx", "monetization": {"rpmEstimationFrom": 2, "rpmEstimationTo": 4}}]

    def search(q):
        if q == "08.25":
            raise RuntimeError("HTTP Error 429")
        return hit
    out = rates.load_rpm(con, dt.date(2026, 11, 14), label=lambda c: c, search=search)
    assert out.startswith("1 of 2 niches priced") and "1 failed" in out
    assert con.execute("SELECT niche_code, rpm_mid FROM niche_rpm").fetchall() == [("03.07", 3.0)]


def test_a_new_niche_is_priced_between_monthly_pulls():
    import datetime as dt
    from terminal import rates, store
    con = store.connect(":memory:")
    con.execute("CREATE TABLE channel_niche AS SELECT * FROM (VALUES ('UCa', '03.07', '03')) t(channel_id, niche_code, sector)")
    hit = [{"id": "UCx", "monetization": {"rpmEstimationFrom": 2, "rpmEstimationTo": 4}}]
    calls = []
    search = lambda q: calls.append(q) or hit
    rates.load_rpm(con, dt.date(2026, 11, 1), label=lambda c: c, search=search)
    con.execute("INSERT INTO channel_niche VALUES ('UCb', '01.68', '01')")
    out = rates.load_rpm(con, dt.date(2026, 11, 14), label=lambda c: c, search=search)
    assert calls == ["03.07", "01.68"] and out.startswith("1 of 1")       # only the new niche is searched
    assert rates.load_rpm(con, dt.date(2026, 11, 15), label=lambda c: c, search=search).startswith("fresh")
    assert sorted(r["niche_code"] for r in rates.latest_rpm(con)) == ["01.68", "03.07"]   # both still priced
