/* Stand-in for the dashboard runtime the terminal page was first built on.
   Datasets come from data/<name>.json (written by `python -m terminal site`); calcs run in the browser. */
window.dash = (() => {
  const raw = {}, calcs = {}, loaders = {}, subs = [];
  let meta = null, failed = null;
  const memo = {};
  function data(name) {
    if (failed) return {status: 'error', error: failed};
    if (name in memo) return memo[name];
    let out;
    if (calcs[name]) {
      const ins = calcs[name].inputs.map(data);
      const bad = ins.find(i => i.status !== 'ok');
      if (bad) return bad;
      try { out = {status: 'ok', data: calcs[name].fn(...ins.map(i => i.data))}; }
      catch (e) { out = {status: 'error', error: String(e)}; }
    } else if (name in raw) {
      let v = raw[name];
      if (loaders[name] && !Array.isArray(v)) {
        try { v = loaders[name].fn(v).rows; } catch (e) { return memo[name] = {status: 'error', error: String(e)}; }
      }
      out = {status: 'ok', data: v};
    } else {
      return {status: 'loading'};
    }
    return memo[name] = out;
  }
  async function load() {
    try {
      const r = await fetch('data/manifest.json', {cache: 'no-store', credentials: 'same-origin'});
      if (r.status === 401 || r.redirected) { location.href = '/login'; return; }
      meta = await r.json();
      await Promise.all(meta.datasets.map(async n => {
        const d = await fetch('data/' + n + '.json', {cache: 'no-store', credentials: 'same-origin'});
        raw[n] = d.ok ? await d.json() : [];
      }));
    } catch (e) { failed = String(e); }
    for (const k in memo) delete memo[k];
    document.dispatchEvent(new CustomEvent('terminal:meta', {detail: meta}));
    subs.forEach(f => f());
  }
  return {
    calc: (name, def) => { calcs[name] = def; },
    loader: (name, def) => { loaders[name] = def; },
    data,
    meta: () => meta,
    setLink: v => { try { history.replaceState(null, '', '#' + v); } catch (e) {} },
    onData: f => { subs.push(f); f(); },
    start: load,
  };
})();
