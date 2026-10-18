"""Screening discovered channels: country rules, model verdicts, human edits stick, seed loads keeps only."""
from types import SimpleNamespace

from terminal import screen

DISC = {c: {"channel": c, "niche": "03.07", "title": t, "subscribers": "50000"} for c, t in
        [("UCus", "Pilates Pat"), ("UCin", "Asli Taaqat"), ("UCgb", "London Lifts"), ("UCbr", "GymCo"), ("UCgone", "Gone")]}
INFO = {"UCus": {"country": "US", "recent_titles": ["20 min pilates"]}, "UCin": {"country": "IN"},
        "UCgb": {"country": "GB"}, "UCbr": {"country": None, "recent_titles": ["Shop our new leggings"]}}


class FakeClient:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(text=self.reply)])


def test_rules_model_and_sticky_edits(tmp_path):
    out = tmp_path / "screened.csv"
    client = FakeClient('[{"id": "UCus", "verdict": "keep", "reason": "ok", "note": "pilates creator"},'
                        ' {"id": "UCbr", "verdict": "drop", "reason": "brand", "note": "apparel company"}]')
    r = screen.run(info_fn=lambda ids: {i: INFO[i] for i in ids if i in INFO}, client=client, discovered=DISC, path=out)
    rows = screen.load(out)
    assert {c: (rows[c]["verdict"], rows[c]["reason"]) for c in rows} == {
        "UCus": ("keep", "ok"), "UCbr": ("drop", "brand"), "UCin": ("drop", "non_us"),
        "UCgb": ("review", "non_us"), "UCgone": ("drop", "other")}
    assert r["screened"] == 5 and client.calls == 1          # only US/unknown-country channels go to the model
    assert screen.kept(out) == {"UCus"}

    # a person flips the UK channel to keep; a second run doesn't touch it or re-screen anything
    rows["UCgb"]["verdict"] = "keep"
    screen.write(rows, out)
    client2 = FakeClient("[]")
    r2 = screen.run(info_fn=lambda ids: {}, client=client2, discovered=DISC, path=out)
    assert r2["screened"] == 0 and client2.calls == 0
    assert screen.kept(out) == {"UCus", "UCgb"}


def test_bad_reply_falls_back_to_review():
    assert screen.parse_reply("not json", ["UCx"]) == {"UCx": ("review", "no answer", "")}
    assert screen.parse_reply('[{"id": "UCx", "verdict": "maybe"}]', ["UCx"])["UCx"][0] == "review"


def test_unanswered_rows_are_retried(tmp_path):
    out = tmp_path / "screened.csv"
    one = {"UCus": DISC["UCus"]}
    info = lambda ids: {i: INFO[i] for i in ids}
    screen.run(info_fn=info, client=FakeClient("cut off before the json"), discovered=one, path=out)
    assert screen.load(out)["UCus"]["reason"] == "no answer"
    screen.run(info_fn=info, client=FakeClient('[{"id": "UCus", "verdict": "keep", "reason": "ok"}]'), discovered=one, path=out)
    assert screen.kept(out) == {"UCus"}


def test_coverage_counts_per_niche():
    import datetime as dt
    from terminal import coverage, store
    con = store.connect(":memory:")
    con.execute("INSERT INTO channels (channel_id, title, added_at) VALUES ('UCus', 'a', ?), ('UCold', 'b', ?), ('UCanc', 'c', ?)",
                [dt.datetime(2026, 10, 18), dt.datetime(2026, 9, 1), dt.datetime(2026, 10, 17)])
    screened = {"UCus": {"niche": "03.07", "verdict": "keep", "reason": "ok"},
                "UCold": {"niche": "03.07", "verdict": "keep", "reason": "ok"},
                "UCin": {"niche": "03.07", "verdict": "drop", "reason": "non_us"},
                "UCx": {"niche": "03.08", "verdict": "review", "reason": "other"}}
    got = {r["niche_code"]: r for r in coverage.rows(con, screened, {"UCanc": "03.08"}, dt.date(2026, 10, 18))}
    assert (got["03.07"]["tracked"], got["03.07"]["added_7d"], got["03.07"]["kept"], got["03.07"]["drop_non_us"]) == (2, 1, 2, 1)
    assert (got["03.08"]["tracked"], got["03.08"]["anchors"], got["03.08"]["review"]) == (1, 1, 1)
