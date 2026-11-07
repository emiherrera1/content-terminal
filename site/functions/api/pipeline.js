// Deal pipeline: each creator's stage and note, shared by everyone with the site password.
// Stored in Cloudflare KV (binding PIPELINE on the Pages project). The password gate in ../_middleware.js runs first,
// so only signed-in viewers reach this. Every change is also appended to a capped log for an audit trail.

const STAGES = ['Watching', 'Contacted', 'In diligence', 'Passed', 'Invested'];
const KEY = 'pipeline', LOG = 'pipeline_log', LOG_MAX = 1000;
const json = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}});

export async function onRequestGet({env}) {
  if (!env.PIPELINE) return json({configured: false, items: {}});
  return json({configured: true, items: (await env.PIPELINE.get(KEY, 'json')) || {}});
}

export async function onRequestPost({request, env}) {
  if (!env.PIPELINE) return json({error: 'Pipeline storage is not connected (KV binding PIPELINE).'}, 503);
  // same-origin JSON only: a form on another site can't send this
  const origin = request.headers.get('Origin');
  if (origin && new URL(origin).host !== new URL(request.url).host) return json({error: 'cross-origin'}, 403);
  if (!(request.headers.get('Content-Type') || '').includes('application/json')) return json({error: 'json only'}, 415);
  let b;
  try { b = await request.json(); } catch { return json({error: 'bad json'}, 400); }
  const id = String(b.channel_id || '');
  if (!/^[A-Za-z0-9_-]{6,64}$/.test(id)) return json({error: 'bad channel id'}, 400);
  const stage = b.stage == null || b.stage === '' ? null : String(b.stage);
  if (stage && !STAGES.includes(stage)) return json({error: 'unknown stage'}, 400);
  const note = String(b.note || '').slice(0, 4000);
  const items = (await env.PIPELINE.get(KEY, 'json')) || {};
  const before = items[id] || {};
  const at = new Date().toISOString();
  if (!stage && !note) delete items[id];
  else items[id] = {stage, note, name: String(b.name || '').slice(0, 200), updated: at, since: before.stage === stage ? (before.since || at) : at};
  await env.PIPELINE.put(KEY, JSON.stringify(items));
  const log = (await env.PIPELINE.get(LOG, 'json')) || [];
  if ((before.stage || null) !== stage || (before.note || '') !== note) {
    log.push({at, channel_id: id, name: String(b.name || '').slice(0, 200), from: before.stage || null, to: stage, note_changed: (before.note || '') !== note});
    await env.PIPELINE.put(LOG, JSON.stringify(log.slice(-LOG_MAX)));
  }
  return json({ok: true, item: items[id] || null});
}
