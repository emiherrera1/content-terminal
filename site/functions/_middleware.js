// Password gate for the whole site (Cloudflare Pages Functions run this before every request, data files included).
// Two passwords live only in Cloudflare (Pages project > Settings > Variables and secrets, type Secret):
//   TERMINAL_PASSWORD        the owner: everything, including the deal pipeline (/api/pipeline)
//   TERMINAL_GUEST_PASSWORD  optional, for visitors such as a grant committee: the screens and data, never the pipeline
// A correct password sets a signed, HttpOnly cookie for 30 days that carries the role. Changing a password logs out
// everyone who used it: guest cookies are signed with both secrets, so a new guest password ends guest sessions only.

const COOKIE = 'ct_session';
const DAYS = 30;
const enc = new TextEncoder();

async function sign(secret, msg) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(msg));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, '0')).join('');
}

function same(a, b) {               // constant-time compare
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

function cookie(req, name) {
  const m = (req.headers.get('Cookie') || '').match(new RegExp('(?:^|;\\s*)' + name + '=([^;]+)'));
  return m ? m[1] : null;
}

const ROLES = ['owner', 'guest'];
const keyFor = (env, role) => role === 'guest' ? env.TERMINAL_PASSWORD + '|' + (env.TERMINAL_GUEST_PASSWORD || '') : env.TERMINAL_PASSWORD;

// the role in a valid session cookie, or null
async function session(req, env) {
  const v = cookie(req, COOKIE);
  if (!v) return null;
  const [exp, role, sig] = v.split('.');
  if (!exp || !sig || !ROLES.includes(role) || Number(exp) < Date.now() / 1000) return null;
  if (role === 'guest' && !env.TERMINAL_GUEST_PASSWORD) return null;
  return same(sig, await sign(keyFor(env, role), 'ct:' + exp + ':' + role)) ? role : null;
}

const HEADERS = {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow',
                 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer'};

function page(msg, status = 200) {
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Content Terminal</title>
<style>
html,body{margin:0;height:100%;background:#000;color:#e8e4da;font-family:"Courier New",ui-monospace,Menlo,monospace}
main{min-height:100%;display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}
form{width:100%;max-width:340px;border:1px solid #2a2724;padding:20px;text-transform:uppercase;letter-spacing:.03em;font-size:12px}
h1{font-size:14px;margin:0 0 16px;color:#ffb340}
label{display:block;margin-bottom:6px;color:#8f8a80}
input{width:100%;box-sizing:border-box;background:#0b0a09;border:1px solid #2a2724;color:#3dff63;font:inherit;font-size:16px;padding:8px}
input:focus{outline:1px solid #ffb340}
button{margin-top:12px;width:100%;background:#ffb340;color:#000;border:0;padding:8px;font:inherit;font-weight:700;cursor:pointer;text-transform:uppercase}
p{color:#ff4d4d;margin:10px 0 0;min-height:1em}
</style></head><body><main>
<form method="post" action="/login">
<h1>Content Terminal</h1>
<label for="pw">Password</label>
<input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
<button type="submit">Enter</button>
<p role="alert">${msg}</p>
</form></main></body></html>`, {status, headers: HEADERS});
}

export async function onRequest(ctx) {
  const {request, env, next} = ctx;
  const url = new URL(request.url);
  const secret = env.TERMINAL_PASSWORD;
  if (!secret) return new Response('Not configured: set TERMINAL_PASSWORD in the Cloudflare Pages project.', {status: 503});

  if (url.pathname === '/logout') {
    return new Response(null, {status: 302, headers: {Location: '/login',
      'Set-Cookie': `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`}});
  }

  if (url.pathname === '/login') {
    if (request.method === 'POST') {
      const form = await request.formData();
      const pw = String(form.get('password') || '');
      // compare digests so the check takes the same time whatever was typed
      const typed = await sign('ct-login', pw);
      const isOwner = same(typed, await sign('ct-login', secret));
      const isGuest = !isOwner && !!env.TERMINAL_GUEST_PASSWORD && same(typed, await sign('ct-login', env.TERMINAL_GUEST_PASSWORD));
      if (isOwner || isGuest) {
        const role = isOwner ? 'owner' : 'guest';
        const exp = Math.floor(Date.now() / 1000) + DAYS * 86400;
        return new Response(null, {status: 303, headers: {Location: '/',
          'Set-Cookie': `${COOKIE}=${exp}.${role}.${await sign(keyFor(env, role), 'ct:' + exp + ':' + role)}; Path=/; Max-Age=${DAYS * 86400}; HttpOnly; Secure; SameSite=Lax`}});
      }
      await new Promise(r => setTimeout(r, 800));   // slow down guessing
      return page('Wrong password', 401);
    }
    if (await session(request, env)) return Response.redirect(url.origin + '/', 302);
    return page('');
  }

  const role = await session(request, env);
  if (!role) {
    if (/^\/(data|data-demo|api)\//.test(url.pathname)) return new Response('Unauthorized', {status: 401, headers: {'Cache-Control': 'no-store'}});
    return Response.redirect(url.origin + '/login', 302);
  }
  if (url.pathname === '/api/whoami') {
    return new Response(JSON.stringify({role}), {headers: {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}});
  }
  // guests see the screens and the data, never the deal pipeline
  if (role !== 'owner' && url.pathname.startsWith('/api/')) {
    return new Response(JSON.stringify({error: 'not available to guests'}), {status: 403, headers: {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}});
  }

  const res = await next();
  const out = new Response(res.body, res);
  out.headers.set('Cache-Control', 'private, no-store');
  out.headers.set('X-Robots-Tag', 'noindex, nofollow');
  out.headers.set('X-Frame-Options', 'DENY');
  out.headers.set('Referrer-Policy', 'no-referrer');
  return out;
}
