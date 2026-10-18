"""Discovery selection logic, offline (search and stats are faked)."""
import csv

from terminal import cli, discover

CFG = {
    "window_days": 60, "pages_per_query": 2, "max_units": 10_000, "target_per_niche": 2,
    "filters": {"min_subscribers": 10_000, "max_subscribers": 2_000_000, "min_videos": 20},
    "niches": {"03.08": ["air fryer recipes", "meal prep"], "03.07": ["pilates workout"]},
}

RESULTS = {
    "air fryer recipes": [["UCa", "UCb", "UCbig"], ["UCa", "UCc"]],  # two pages
    "meal prep": [["UCa", "UCtiny", "UCd"]],
    "pilates workout": [["UCp", "UCd", "UCfew"]],
}

STATS = {
    "UCa": {"title": "A", "subscribers": 50_000, "videos": 100, "hidden": False},
    "UCb": {"title": "B", "subscribers": 900_000, "videos": 300, "hidden": False},
    "UCc": {"title": "C", "subscribers": 20_000, "videos": 40, "hidden": False},
    "UCd": {"title": "D", "subscribers": 30_000, "videos": 80, "hidden": False},
    "UCp": {"title": "P", "subscribers": 15_000, "videos": 60, "hidden": True},
    "UCbig": {"title": "Big", "subscribers": 5_000_000, "videos": 900, "hidden": False},
    "UCtiny": {"title": "Tiny", "subscribers": 2_000, "videos": 50, "hidden": False},
    "UCfew": {"title": "Few", "subscribers": 40_000, "videos": 5, "hidden": False},
}


def fake_search():
    calls = []

    def search(q, after, region, language, token):
        page = int(token or 0)
        calls.append(q)
        pages = RESULTS[q]
        nxt = str(page + 1) if page + 1 < len(pages) else None
        return pages[page], nxt
    return search, calls


def test_filters_assignment_and_target(tmp_path):
    search, calls = fake_search()
    out = tmp_path / "found.csv"
    r = discover.run(CFG, search, lambda ids: {i: STATS[i] for i in ids}, out)
    rows = {row["channel"]: row for row in csv.DictReader(open(out))}
    # too big, too small, too few videos and hidden subscriber counts are dropped
    assert not {"UCbig", "UCtiny", "UCfew", "UCp"} & rows.keys()
    # target of 2 per niche: food keeps UCa (hit 3 times), then the smallest of the one-hit channels
    food = sorted(c for c, row in rows.items() if row["niche"] == "03.08")
    assert food == ["UCa", "UCc"] and rows["UCa"]["hits"] == "3"
    # UCd showed up once in each niche; ties go to the first niche seen (food), so pilates doesn't get it
    assert "UCd" not in rows
    assert r["units"] == 4 * discover.SEARCH_COST + 1 and len(calls) == 4


def test_merges_with_earlier_runs_without_duplicates(tmp_path):
    out = tmp_path / "found.csv"
    search, _ = fake_search()
    discover.run(CFG, search, lambda ids: {i: STATS[i] for i in ids}, out)
    first = list(csv.DictReader(open(out)))
    search, _ = fake_search()
    r = discover.run(CFG, search, lambda ids: {i: STATS[i] for i in ids}, out)
    assert r["added"] == 0 and r["total"] == len(first)


def test_stops_at_quota_budget():
    search, calls = fake_search()
    hits, units = discover.search_hits({**CFG, "max_units": 250}, search)
    assert units == 200 and len(calls) == 2


def test_seed_reads_both_lists_and_skips_comments(tmp_path, monkeypatch):
    a = tmp_path / "channels.csv"
    a.write_text("channel,niche,role,note\n# Fitness\n@one,03.07,anchor,\nUCtwo,03.08,anchor,\n")
    b = tmp_path / "channels_discovered.csv"
    b.write_text("channel,niche\nUCtwo,03.08\nUCthree,03.07\nUCdropped,03.07\n")
    monkeypatch.setattr(cli.screen, "kept", lambda: {"UCtwo", "UCthree"})   # UCdropped was screened out
    monkeypatch.setattr(cli.youtube, "resolve_handle", lambda h: "UCone" if h == "@one" else None)
    seen = {}
    monkeypatch.setattr(cli.youtube, "pull_channels", lambda con, ids, today: seen.setdefault("ids", ids) and len(ids))
    cli.seed(None, (a, b, tmp_path / "missing.csv"))
    assert seen["ids"] == ["UCone", "UCtwo", "UCthree"]
