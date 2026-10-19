"""One-off: turn the terminal page (written for the dashboard runtime) into site/public/index.html.
Kept so the port is reproducible; edit site/public/index.html directly from now on."""
import sys
from pathlib import Path

src, here = Path(sys.argv[1]).read_text(), Path(__file__).parent
shim = (here / "dash-shim.js").read_text()
swaps = [
    ('<span><span class="dot">●</span> Sample data</span>',
     '<span><span class="dot" id="src-dot">●</span> <span id="src-label">Loading</span></span>'),
    ('<span class="ver">Content terminal v0.4</span>',
     '<span class="ver">Content terminal v0.5 · <a href="/logout" style="color:inherit">Log out</a></span>'),
    ("say('Welcome to Content Terminal v0.4 (sample data)');", "say('Welcome to Content Terminal v0.5');"),
    ('Read live from the Trending Formats sheet each time the terminal opens.', 'Refreshed from the Trending Formats sheet each morning.'),
    ("say(arg ? 'Formats matching ' + arg.toUpperCase() : 'Title formats, live from Google Sheets');",
     "say(arg ? 'Formats matching ' + arg.toUpperCase() : 'Title formats, from the Trending Formats sheet');"),
]
for a, b in swaps:
    assert src.count(a) == 1, a
    src = src.replace(a, b)
meta_js = """<script>
document.addEventListener('terminal:meta', e => {
  const m = e.detail || {}, lab = document.getElementById('src-label'), dot = document.getElementById('src-dot');
  const synthetic = m.source === 'synthetic';
  lab.textContent = !m.source ? 'No data' : synthetic ? 'SYNTHETIC DATA: made-up channels, not results' : 'Live data';
  dot.style.color = synthetic ? 'var(--amb)' : m.source ? 'var(--g)' : 'var(--red)';
  if (synthetic) document.getElementById('terminal').style.outline = '2px solid var(--amb)';
});
dash.start();
</script>"""
page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Content Terminal</title>
<style>html,body{{margin:0;background:#000;color:#e8e4da}} body{{padding:8px}} @media (max-width:600px){{body{{padding:0}}}}</style>
<script src="vendor/d3.min.js"></script>
<script>{shim}</script>
</head><body>
{src}
{meta_js}
</body></html>
"""
(here / "public" / "index.html").write_text(page)
print(len(page))
