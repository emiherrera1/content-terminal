"""Business signals from video descriptions: how each creator makes money beyond ads, and who sponsors them.

Free: it reads the descriptions and the paid-promotion flag the daily YouTube pull already stores.

  video_signals     one row per video: paid placement flag, sponsor wording, sponsor brands, revenue streams linked
  creator_business  one row per creator (uploads in the last 90 days): sponsored share, top sponsors, revenue streams
  sponsor_map       one row per sponsor brand: creators and videos, first and last seen, niches

How a link is read. Every URL in a description is reduced to its host and sorted into one bucket:
  social and reference links (Instagram, a study on PubMed) are ignored;
  known hosts map to a stream: affiliate (Amazon, ShopMy, LTK), membership (Patreon, Ko-fi), course or community
  (Skool, Teachable, Stan), newsletter (Substack, beehiiv), services (Calendly, Stripe checkout), crowdfunding;
  a host only this creator links to, in at least 30% of their videos (5 or more), is their own site or store;
  any other commercial host is a sponsor when YouTube's sponsor-link domain (brand.yt.link) is used, when the
  description has sponsor wording (sponsor, use code, promo code, % off), or when two or more tracked creators
  link to it. Link shorteners (bit.ly) cannot be read without opening them, so they are left out.
Everything here is detected, not reported: a creator can run a stream the descriptions never mention.
"""
import datetime as dt
import re
from collections import Counter, defaultdict

STREAMS = ["Sponsorships", "Affiliate", "Membership", "Course or community", "Newsletter", "Own site or store",
           "Services", "Crowdfunding"]

IGNORE = {"youtube.com", "youtu.be", "instagram.com", "tiktok.com", "facebook.com", "fb.com", "twitter.com", "x.com",
          "threads.net", "snapchat.com", "linkedin.com", "pinterest.com", "pin.it", "discord.gg", "discord.com",
          "twitch.tv", "open.spotify.com", "spotify.com", "podcasts.apple.com", "music.apple.com", "apple.co",
          "reddit.com", "linktr.ee", "beacons.ai", "t.me", "wa.me", "whatsapp.com", "tumblr.com", "vimeo.com",
          "soundcloud.com", "google.com", "docs.google.com", "drive.google.com", "forms.gle", "goo.gl", "maps.google.com",
          "github.com", "wikipedia.org", "en.wikipedia.org", "goodreads.com", "imdb.com", "archive.org",
          "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "doi.org", "nejm.org", "nature.com",
          "sciencedirect.com", "academic.oup.com", "jamanetwork.com", "thelancet.com", "bmj.com", "cdc.gov", "nih.gov",
          "apps.apple.com", "play.google.com", "bit.ly", "tinyurl.com", "shorturl.at", "ow.ly", "buff.ly", "rebrand.ly",
          "cutt.ly", "is.gd", "lnk.to", "smarturl.it", "hyperurl.co", "glnk.io", "opr.as", "dada.link", "linkin.bio",
          # research, news, reference and other links that cite rather than pay
          "apple.com", "bsky.app", "lickd.co", "creativecommons.org", "researchgate.net", "springer.com", "wiley.com",
          "frontiersin.org", "mdpi.com", "lww.com", "ahajournals.org", "tandfonline.com", "cell.com", "science.org",
          "plos.org", "journals.plos.org", "biorxiv.org", "medrxiv.org", "arxiv.org", "ssrn.com", "jstor.org",
          "nytimes.com", "wsj.com", "bloomberg.com", "cnbc.com", "reuters.com", "bbc.com", "bbc.co.uk", "theguardian.com",
          "washingtonpost.com", "cnn.com", "forbes.com", "apnews.com", "npr.org", "economist.com", "ft.com", "axios.com",
          "statista.com", "who.int", "mayoclinic.org", "clevelandclinic.org", "examine.com", "healthline.com", "webmd.com"}
# public-sector, academic and non-profit domains never count as sponsors
IGNORE_SUFFIX = (".gov", ".edu", ".mil", ".int", ".ac.uk", ".gov.uk", ".edu.au", ".org")
# deep-link services put the brand in the first label (headspace-web.app.link)
DEEP_LINK = (".yt.link", ".app.link", ".onelink.me", ".page.link", ".sng.link", ".test-app.link")
NOT_A_BRAND = {"onelink", "go", "app", "yt", "link", "www", "l", "s", "t", "m", "on", "get", "try", "join", "ref", "shop", "store", "my"}
STREAM_HOSTS = {
    "Affiliate": ["amzn.to", "amazon.com", "amazon.ca", "amazon.co.uk", "link.amazon", "a.co", "amzlink.to", "geni.us",
                  "shopmy.us", "go.shopmy.us", "go.shopmy", "shopltk.com", "on.ltk.com", "liketoknow.it", "ltk.app",
                  "tidd.ly", "go.magik.ly", "rstyle.me", "howl.me", "howl.link", "kit.co", "share.epidemicsound.com",
                  "skimresources.com", "go.skimresources.com", "awin1.com", "click.linksynergy.com", "bestbuy.com",
                  # affiliate networks (Impact, CJ, Partnerize, Levanta, ShareASale) and referral programs
                  "pxf.io", "sjv.io", "7eer.net", "ojrq.net", "evyy.net", "mtko.net", "anrdoezrs.net", "jdoqocy.com",
                  "tkqlhce.com", "dpbolvw.net", "kqzyfj.com", "prf.hn", "lvnta.com", "shareasale.com", "avantlink.com",
                  "pjtra.com", "epidemicsound.com", "storyblocks.com", "artlist.io"],
    "Membership": ["patreon.com", "ko-fi.com", "buymeacoffee.com", "memberful.com", "supporter.fm", "fourthwall.com/supporters"],
    "Course or community": ["skool.com", "teachable.com", "thinkific.com", "kajabi.com", "mykajabi.com", "podia.com",
                            "stan.store", "gumroad.com", "maven.com", "circle.so", "udemy.com", "learnworlds.com"],
    "Newsletter": ["substack.com", "beehiiv.com", "kit.com", "convertkit.com", "ck.page", "list-manage.com", "ghost.io",
                   "mailchi.mp"],
    "Services": ["calendly.com", "buy.stripe.com", "cal.com", "acuityscheduling.com", "tidycal.com"],
    "Crowdfunding": ["kickstarter.com", "indiegogo.com", "gofundme.com"],
    "Own site or store": ["myshopify.com", "fourthwall.com", "teespring.com", "spring.com", "creator-spring.com",
                          "represent.com", "bonfire.com", "spreadshirt.com", "merchbar.com"],
}
_STREAM_OF = {h: s for s, hs in STREAM_HOSTS.items() for h in hs}
SPONSOR_WORDS = re.compile(r"\bsponsor|\buse (?:my )?code\b|\bpromo code\b|\bdiscount code\b|\d+\s?% off\b|\bcode\s+[A-Z0-9]{3,}", re.I)
URL = re.compile(r"https?://(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})(/[^\s)\]>\"']*)?", re.I)
BRAND_PREFIX = re.compile(r"^(?:go|get|try|join|ref|shop|store|my|link|links|app|buy|offer|offers|partners?|click|l)\.", re.I)
ALIASES = {"drinklmnt": "LMNT", "ankerfast": "Anker", "monarchmoney": "Monarch Money", "joindeleteme": "DeleteMe",
           "rocketmoney": "Rocket Money", "helixsleep": "Helix Sleep", "betterhelp": "BetterHelp", "nordvpn": "NordVPN",
           "expressvpn": "ExpressVPN", "surfshark": "Surfshark", "squarespace": "Squarespace", "athleticgreens": "AG1",
           "drinkag1": "AG1", "eightsleep": "Eight Sleep", "thrivemarket": "Thrive Market", "groundnews": "Ground News",
           "ground": "Ground News", "usenourish": "Nourish", "wisprflow": "Wispr Flow", "soundcore": "Anker Soundcore",
           "ugreen": "UGREEN", "dbrand": "dbrand", "hostinger": "Hostinger", "incogni": "Incogni", "saily": "Saily",
           "quince": "Quince", "bombas": "Bombas", "whoop": "WHOOP", "airalo": "Airalo", "odoo": "Odoo", "seed": "Seed",
           "ritual": "Ritual", "cometeer": "Cometeer", "casetify": "CASETiFY", "paperlike": "Paperlike", "chime": "Chime",
           "curology": "Curology", "headspace": "Headspace", "functionhealth": "Function Health", "timeline": "Timeline", "zocdoc": "Zocdoc", "webull": "Webull", "autods": "AutoDS"}
_TLD2 = {"co.uk", "com.au", "co.nz", "com.br", "co.jp"}


def host_of(h: str) -> str:
    return h.lower().strip(".")


def stream_of(h: str, path: str = "") -> str | None:
    if h in _STREAM_OF:
        return _STREAM_OF[h]
    for k, s in _STREAM_OF.items():
        if "/" not in k and h.endswith("." + k):
            return s
    if (h in ("youtube.com", "m.youtube.com")) and path.rstrip("/").endswith("/join"):
        return "Membership"
    return None


def ignored(h: str) -> bool:
    return h in IGNORE or any(h.endswith("." + i) for i in IGNORE) or h.endswith(IGNORE_SUFFIX)


def brand_of(h: str) -> str | None:
    """drinklmnt.com -> LMNT; monarchmoney.yt.link -> Monarch Money; shop.anker.com -> Anker; None when no brand shows."""
    dl = next((d for d in DEEP_LINK if h.endswith(d)), None)
    if dl:
        core = h[: -len(dl)].split(".")[-1].split("-")[0]
    else:
        parts = h.split(".")
        tail = 3 if ".".join(parts[-2:]) in _TLD2 else 2
        core = BRAND_PREFIX.sub("", ".".join(parts[:-tail + 1] if len(parts) > tail else parts[:1]))
        core = core.split(".")[-1] if core else parts[0]
    if not core or core.lower() in NOT_A_BRAND:
        return None
    return ALIASES.get(core.lower(), core[:1].upper() + core[1:])


def links(desc: str) -> list[tuple[str, str]]:
    # a host still starting with www. is a link cut off mid-word (https://www.instagra...), so it is dropped
    return [(host_of(h), p or "") for h, p in URL.findall(desc or "") if not host_of(h).startswith("www.")]


def build(con, today: dt.date | None = None, window_days: int = 90) -> str:
    today = today or dt.date.today()
    has_flag = bool(con.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'videos' "
                                "AND column_name = 'paid_placement'").fetchone()[0])
    rows = con.execute(f"""SELECT video_id, channel_id, published_at, coalesce(description, ''),
                                  {'paid_placement' if has_flag else 'NULL'} FROM videos""").fetchall()
    # pass 1: which hosts each creator links to, and how widely each host is used
    per_video, host_channels, chan_videos, chan_host_videos = [], defaultdict(set), Counter(), defaultdict(Counter)
    for vid, ch, pub, desc, flag in rows:
        ls = [(h, p) for h, p in links(desc) if not ignored(h) or stream_of(h, p)]
        hosts = {h for h, _ in ls}
        per_video.append((vid, ch, pub, desc, flag, ls))
        chan_videos[ch] += 1
        for h in hosts:
            host_channels[h].add(ch)
            chan_host_videos[ch][h] += 1
    own = {(ch, h) for ch, c in chan_host_videos.items() for h, k in c.items()
           if len(host_channels[h]) == 1 and k >= 5 and k / chan_videos[ch] >= 0.3 and not stream_of(h)}
    # pass 2: classify each video
    vrows = []
    for vid, ch, pub, desc, flag, ls in per_video:
        words = bool(SPONSOR_WORDS.search(desc))
        streams, sponsors = set(), set()
        for h, p in ls:
            s = stream_of(h, p)
            if s:
                streams.add(s)
            elif (ch, h) in own:
                streams.add("Own site or store")
            elif h.endswith(DEEP_LINK) or words or len(host_channels[h]) >= 2:
                b = brand_of(h)
                if b:
                    sponsors.add(b)
        if sponsors or flag:
            streams.add("Sponsorships")
        vrows.append((vid, ch, pub, flag, words, "; ".join(sorted(sponsors)) or None, "; ".join(s for s in STREAMS if s in streams) or None))
    con.execute("""CREATE OR REPLACE TABLE video_signals (video_id VARCHAR, channel_id VARCHAR, published_at TIMESTAMP,
                   paid_placement BOOLEAN, sponsor_words BOOLEAN, sponsors VARCHAR, streams VARCHAR)""")
    if vrows:
        con.executemany("INSERT INTO video_signals VALUES (?, ?, ?, ?, ?, ?, ?)", vrows)
    since = today - dt.timedelta(days=window_days)
    con.execute(f"""CREATE OR REPLACE TABLE creator_business AS
        WITH v AS (SELECT * FROM video_signals WHERE published_at >= DATE '{since}'),
             s AS (SELECT channel_id, trim(unnest(string_split(sponsors, ';'))) AS brand FROM v WHERE sponsors IS NOT NULL),
             top AS (SELECT channel_id, list(brand ORDER BY n DESC, brand)[1:5] AS sponsors
                     FROM (SELECT channel_id, brand, count(*) AS n FROM s GROUP BY ALL) GROUP BY 1),
             st AS (SELECT channel_id, list(DISTINCT trim(x)) AS streams
                    FROM (SELECT channel_id, unnest(string_split(streams, ';')) AS x FROM v WHERE streams IS NOT NULL) GROUP BY 1)
        SELECT v.channel_id, count(*) AS videos_90d,
               count(*) FILTER (WHERE v.sponsors IS NOT NULL OR v.paid_placement) AS sponsored_videos_90d,
               count(*) FILTER (WHERE v.sponsors IS NOT NULL OR v.paid_placement) / count(*) AS sponsored_share,
               count(*) FILTER (WHERE v.paid_placement) AS paid_placement_videos,
               max(v.published_at) FILTER (WHERE v.sponsors IS NOT NULL OR v.paid_placement)::DATE AS last_sponsored,
               any_value(top.sponsors) AS top_sponsors, any_value(st.streams) AS streams
        FROM v LEFT JOIN top USING (channel_id) LEFT JOIN st USING (channel_id) GROUP BY v.channel_id""")
    has_niche = bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'channel_niche'").fetchone()[0])
    con.execute(f"""CREATE OR REPLACE TABLE sponsor_map AS
        WITH s AS (SELECT channel_id, published_at, trim(unnest(string_split(sponsors, ';'))) AS brand
                   FROM video_signals WHERE sponsors IS NOT NULL)
        SELECT brand, count(DISTINCT s.channel_id) AS creators, count(*) AS videos,
               count(*) FILTER (WHERE published_at >= DATE '{since}') AS videos_90d,
               min(published_at)::DATE AS first_seen, max(published_at)::DATE AS last_seen,
               {'list(DISTINCT n.niche_code)' if has_niche else 'NULL'} AS niche_codes
        FROM s {'LEFT JOIN channel_niche n USING (channel_id)' if has_niche else ''}
        GROUP BY brand ORDER BY creators DESC, videos DESC""")
    n_c = con.execute("SELECT count(*) FROM creator_business").fetchone()[0]
    n_b = con.execute("SELECT count(*) FROM sponsor_map WHERE creators >= 2").fetchone()[0]
    return f"{len(vrows)} videos read, {n_c} creators with uploads in {window_days} days, {n_b} sponsors seen at 2+ creators"
