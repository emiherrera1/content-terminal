"""Creator Capital daily notes into the News view."""
import json

from terminal import notes, news, site, store

SHEET = """Date,Category,Label,Headline,Note,Visual,Sources,Flags,Status,Gmail Thread ID,QUE'D ,IMAGE MADE,POSTED,IN BRIEF
2026-11-05,Platform Change,New Platform Feature,YouTube opens Shopping to small channels,"NEWS: YouTube opened Shopping.

What it means for creators: more ways to earn.",None,"Scalable — https://scalablepod.com/p/one
Other — https://example.com/two",PRIVATE CHECK NOTE,Draft,abc123,FALSE,FALSE,TRUE,FALSE
2026-11-05,Fundraise Announcement,New Raise,Studio raises $5M,NEWS: A studio raised.,None,Scalable — https://scalablepod.com/p/one,another private flag,Draft,def456,FALSE,FALSE,FALSE,FALSE
2026-11-04,Other,News,No link story,NEWS: something.,None,Spoken on a podcast,,Draft,,FALSE,FALSE,FALSE,FALSE
"""


def test_notes_load_without_private_columns(tmp_path, monkeypatch):
    con = store.connect(":memory:")
    news.connect(con)
    monkeypatch.delenv("NOTES_SHEET_CSV_URL", raising=False)
    monkeypatch.setattr(notes, "LOCAL", tmp_path / "n.csv")
    (tmp_path / "n.csv").write_text(SHEET)
    assert notes.load(con) == 3
    rows = con.execute("SELECT url, category, label, status, note FROM news ORDER BY published_at DESC, title").fetchall()
    # two notes from the same newsletter issue stay separate
    assert len({r[0] for r in rows}) == 3 and all(r[0].startswith("https://scalablepod.com/p/one#cc-") for r in rows[:2])
    assert rows[2][0].startswith("creator-capital:")                        # no link in Sources
    assert {r[1] for r in rows} == {"PLATFORM", "DEAL", None}               # "Other" is left for the tagger
    assert [r[3] for r in rows].count("posted") == 1
    everything = json.dumps(con.execute("SELECT * FROM news").fetchall(), default=str)
    assert "PRIVATE CHECK NOTE" not in everything and "abc123" not in everything and "another private flag" not in everything


def test_drafts_can_be_left_out_and_removed_notes_leave(monkeypatch):
    rows = notes.parse(SHEET, include_drafts=False)
    assert [r["title"] for r in rows] == ["YouTube opens Shopping to small channels"]
    con = store.connect(":memory:")
    news.connect(con)
    for r in notes.parse(SHEET):
        con.execute("INSERT INTO news (url, source, title, published_at, status) VALUES (?, ?, ?, ?, ?)",
                    [r["url"], r["source"], r["title"], r["published_at"], r["status"]])
    con.execute("INSERT INTO news (url, source, title) VALUES ('https://blog.youtube/x', 'YouTube Official Blog', 'Feed item')")
    monkeypatch.setattr(notes, "_text", lambda: SHEET.split("2026-11-04")[0])   # the third note was deleted from the sheet
    notes.load(con)
    titles = {t for (t,) in con.execute("SELECT title FROM news").fetchall()}
    assert "No link story" not in titles and "Feed item" in titles          # feed items are never touched


def test_site_news_carries_the_note():
    con = store.connect(":memory:")
    news.connect(con)
    for r in notes.parse(SHEET):
        con.execute("INSERT INTO news (url, source, title, summary, published_at, category, note, label, status, impact) "
                    "VALUES (?, ?, ?, ?, ?, coalesce(?, 'TREND'), ?, ?, ?, 'MEDIUM')",
                    [r["url"], r["source"], r["title"], r["summary"], r["published_at"], r["category"], r["note"], r["label"], r["status"]])
    out = site.news(con)
    first = next(n for n in out if n["label"] == "New Platform Feature")
    assert first["note"].startswith("NEWS: YouTube opened Shopping") and first["status"] == "posted" and first["source"] == "Creator Capital"
