"""Maps: HTTP API, MCP and view server over one service layer (maps.py).

Stdlib only. The Wizard gateway owns authentication; the direct port stays loopback. Map engines (search, routing,
transit) are sidecar services on the app's own network and are never published.
"""
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import maps  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = maps.DATA
APP_ID = 'maps'
VERSION = '0.1.0'
STATIC_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml',
                '.png': 'image/png', '.json': 'application/json', '.pbf': 'application/x-protobuf'}
Problem = maps.Problem

# ---- MCP ---------------------------------------------------------------------------------------------------------
_PT = {'type': 'string', 'description': '"lat,lon" (e.g. "13.7466,100.5393") or a place name to look up.'}
TOOLS = [
    {'name': 'search_places', 'description': 'Search places and addresses on the owner\'s own map server (regions they '
     'added). Optional lat/lon bias results to nearby. Returns name, address, lat, lon, kind.',
     'inputSchema': {'type': 'object', 'properties': {'q': {'type': 'string', 'maxLength': 200}, 'lat': {'type': 'number'},
                                                      'lon': {'type': 'number'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                                                      'lang': {'type': 'string', 'description': '2-letter language for names, e.g. en, th'}},
                     'required': ['q']}},
    {'name': 'reverse_geocode', 'description': 'What is at a coordinate (nearest address or place).',
     'inputSchema': {'type': 'object', 'properties': {'lat': {'type': 'number'}, 'lon': {'type': 'number'}},
                     'required': ['lat', 'lon']}},
    {'name': 'route', 'description': 'Directions between two points by car, motorbike, bike, walk or transit (public '
     'transport from official timetables). Returns distance, duration and turn-by-turn steps or transit legs. ETAs have '
     'no live traffic.',
     'inputSchema': {'type': 'object', 'properties': {'from': _PT, 'to': _PT,
                                                      'mode': {'type': 'string', 'enum': ['car', 'motorbike', 'bike', 'walk', 'transit']},
                                                      'depart_at': {'type': 'string', 'description': 'Transit only: ISO time with offset, e.g. 2026-11-19T09:00:00+07:00. Default now.'}},
                     'required': ['from', 'to']}},
    {'name': 'save_place', 'description': 'Save a place to a list (default "Saved"; a new list name creates the list). '
     'Pass the returned id again to change it: same id = same place (idempotent).',
     'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string', 'maxLength': 120}, 'lat': {'type': 'number'},
                                                      'lon': {'type': 'number'}, 'address': {'type': 'string', 'maxLength': 300},
                                                      'note': {'type': 'string', 'maxLength': 1000}, 'list': {'type': 'string', 'maxLength': 40},
                                                      'id': {'type': 'string', 'description': 'Optional stable id (6-64 letters/digits/dashes).'}},
                     'required': ['name', 'lat', 'lon']}},
    {'name': 'list_places', 'description': 'The owner\'s saved places, optionally one list or matching text.',
     'inputSchema': {'type': 'object', 'properties': {'list': {'type': 'string'}, 'q': {'type': 'string'}}}},
    {'name': 'delete_place', 'description': 'Remove one saved place by id.',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}},
    {'name': 'regions', 'description': 'Which map regions are on the owner\'s server and how far each build is '
     '(state queued/building/ready/failed, step, pct), plus the world overview map and engine states.',
     'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'find_region', 'description': 'Look up downloadable regions (countries, states, provinces) by name or by '
     'a coordinate. Use the id with add_region.',
     'inputSchema': {'type': 'object', 'properties': {'q': {'type': 'string'}, 'lat': {'type': 'number'}, 'lon': {'type': 'number'}}}},
    {'name': 'add_region', 'description': 'Add a region to the owner\'s map server: detailed map, search, directions and '
     'transit get built in the background (minutes to an hour for a country). Idempotent. Check progress with regions.',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string', 'description': 'Region id from find_region, e.g. thailand'}},
                     'required': ['id']}},
    {'name': 'remove_region', 'description': 'Remove a region and its data from the owner\'s server. Ask the owner first.',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}},
    {'name': 'refresh_region', 'description': 'Rebuild a region from the newest map data now (normally automatic monthly).',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}},
]


def call_tool(n, a):
    if n == 'search_places':
        return maps.search(a.get('q'), a.get('lat'), a.get('lon'), a.get('limit'), a.get('lang'))
    if n == 'reverse_geocode':
        return maps.reverse(a.get('lat'), a.get('lon'), a.get('lang'))
    if n == 'route':
        r = maps.route(a.get('from'), a.get('to'), a.get('mode') or 'car', a.get('depart_at'))
        for x in r['routes']:                       # agents get the steps, not thousands of coordinates
            x.pop('geometry', None)
            for leg in x.get('legs', []):
                leg.pop('geometry', None)
        return r
    if n == 'save_place':
        return maps.save_place(a)
    if n == 'list_places':
        return maps.list_places(a.get('list'), a.get('q'))
    if n == 'delete_place':
        return maps.delete_place(str(a.get('id', '')))
    if n == 'regions':
        return maps.regions_doc()
    if n == 'find_region':
        return maps.find_regions(a.get('q'), a.get('lat'), a.get('lon'))
    if n == 'add_region':
        return maps.add_region(a.get('id'))
    if n == 'remove_region':
        return maps.remove_region(a.get('id'))
    if n == 'refresh_region':
        return maps.refresh_region(a.get('id'))
    return None


def rpc(msg):
    if not isinstance(msg, dict):
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Expected a JSON-RPC object.'}}
    m, i, p = msg.get('method'), msg.get('id'), msg.get('params') or {}
    if 'id' not in msg:
        return None
    try:
        if m == 'initialize':
            r = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}}, 'serverInfo': {'name': APP_ID, 'version': VERSION}}
        elif m == 'tools/list':
            r = {'tools': TOOLS}
        elif m == 'tools/call':
            value = call_tool(p.get('name'), p.get('arguments') or {})
            if value is None:
                return {'jsonrpc': '2.0', 'id': i, 'error': {'code': -32602, 'message': 'Unknown tool.'}}
            r = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}], 'structuredContent': value, 'isError': False}
        elif m == 'ping':
            r = {}
        else:
            return {'jsonrpc': '2.0', 'id': i, 'error': {'code': -32601, 'message': 'Unknown method.'}}
    except Problem as e:
        r = {'content': [{'type': 'text', 'text': e.message}], 'isError': True}
    except Exception as e:  # noqa: BLE001 - report, never crash the MCP connection
        r = {'content': [{'type': 'text', 'text': f'Maps error: {e}'}], 'isError': True}
    return {'jsonrpc': '2.0', 'id': i, 'result': r}


def _csv(name):
    return {x.strip().lower() for x in os.environ.get(name, '').split(',') if x.strip()}


# Who may talk to this app. Trust never comes from the request itself: the Host must be one this app was configured
# for (stops DNS rebinding), and a browser Origin on writes must be configured too.
_LOOPBACK = {f"127.0.0.1:{os.environ.get('PORT', '8799')}", f"localhost:{os.environ.get('PORT', '8799')}"}
TRUSTED_HOSTS = _csv('WIZARD_TRUSTED_HOSTS') | _csv('WIZARD_APP_HOSTS') | _LOOPBACK
TRUSTED_ORIGINS = _csv('WIZARD_TRUSTED_ORIGINS') | {'http://' + h for h in _LOOPBACK}
CSP = ("default-src 'self'; script-src 'self'; worker-src 'self' blob:; child-src 'self' blob:; "
       "img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; connect-src 'self' data: blob:; "
       "object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
TILE_RE = re.compile(r'/api/(tiles|packs)/([a-z0-9-]{2,80})\.pmtiles')
ASSET_RE = re.compile(r'/api/assets/(fonts/[A-Za-z0-9 ]{3,60}/\d{1,5}-\d{1,5}\.pbf|sprites/v4/[a-z]+(@2x)?\.(json|png))')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def refused(self, write=False):
        if (self.headers.get('Host') or '').lower() not in TRUSTED_HOSTS:
            self.send(403, {'error': 'Unknown host.'})
            return True
        if write:
            origin = (self.headers.get('Origin') or '').lower()
            if origin and origin not in TRUSTED_ORIGINS:
                self.send(403, {'error': 'Cross-origin writes refused.'})
                return True
            if not origin and (self.headers.get('Sec-Fetch-Site') or '') == 'cross-site':
                self.send(403, {'error': 'Cross-origin writes refused.'})
                return True
        return False

    def send(self, code, body=None, content_type='application/json', cache='no-store', extra=None):
        b = body if isinstance(body, bytes) else b'' if body is None else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(b)

    def send_file(self, path, content_type, cache='no-cache'):
        """A file with HTTP Range support (PMTiles reads archives by byte range)."""
        st = path.stat()
        size, etag = st.st_size, f'"{int(st.st_mtime)}-{st.st_size}"'
        rng = self.headers.get('Range', '')
        m = re.fullmatch(r'bytes=(\d*)-(\d*)', rng.strip()) if rng else None
        if self.headers.get('If-Range') and self.headers.get('If-Range') != etag:
            m = None
        start, end, code = 0, size - 1, 200
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start >= size or start > end:
                return self.send(416, None, extra={'Content-Range': f'bytes */{size}'})
            code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('ETag', etag)
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        with open(path, 'rb') as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def body(self):
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise Problem(415, 'Use application/json.')
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 <= size <= 65536:
            raise Problem(413, 'Request too large.')
        return json.loads(self.rfile.read(size) or b'{}')

    def do_GET(self):
        if self.refused():
            return
        u = urlsplit(self.path)
        path, q = u.path, {k: v[-1] for k, v in parse_qs(u.query).items()}
        try:
            if path == '/healthz':
                return self.send(200, {'ok': True, 'app': APP_ID, 'version': VERSION})
            if path == '/api/state':
                return self.send(200, dict(maps.regions_doc(), **maps.lists(), **maps.list_places(), version=VERSION))
            if path == '/api/regions':
                return self.send(200, maps.regions_doc())
            if path == '/api/regions/catalog':
                return self.send(200, maps.find_regions(q.get('q'), q.get('lat'), q.get('lon')))
            if path == '/api/search':
                return self.send(200, maps.search(q.get('q'), q.get('lat'), q.get('lon'), q.get('limit'), q.get('lang')))
            if path == '/api/reverse':
                return self.send(200, maps.reverse(q.get('lat'), q.get('lon'), q.get('lang')))
            if path == '/api/places':
                return self.send(200, maps.list_places(q.get('list'), q.get('q')))
            if path == '/api/lists':
                return self.send(200, maps.lists())
            if path == '/api/packs':
                return self.send(200, maps.packs())
            if path == '/api/packs/estimate':
                return self.send(200, maps.estimate_pack(q.get('bbox'), q.get('maxzoom')))
            m = TILE_RE.fullmatch(path)
            if m:
                f = DATA / m.group(1) / f'{m.group(2)}.pmtiles'
                if f.is_file():
                    return self.send_file(f, 'application/octet-stream')
                return self.send(404, {'error': 'No such map.'})
            m = ASSET_RE.fullmatch(unquote(path))
            if m:
                f = DATA / 'assets' / m.group(1)
                if f.is_file():
                    return self.send(200, f.read_bytes(), STATIC_TYPES[f.suffix], cache='max-age=604800')
                return self.send(404, {'error': 'Not found.'})
        except Problem as e:
            return self.send(e.code, {'error': e.message})
        except (ValueError, TypeError):
            return self.send(400, {'error': 'Invalid request.'})
        except Exception as e:  # noqa: BLE001
            return self.send(502, {'error': f'Maps could not do that: {e}'})
        # The view: files in web/ and web/vendor/ by an allowlisted type. No other subpaths, no dotfiles.
        name = 'index.html' if path == '/' else path[1:]
        parts = name.split('/')
        kind = STATIC_TYPES.get(Path(name).suffix)
        if kind and (len(parts) == 1 or (len(parts) == 2 and parts[0] == 'vendor')) and not any(
                p.startswith('.') or not p for p in parts) and (ROOT / 'web' / name).is_file():
            return self.send(200, (ROOT / 'web' / name).read_bytes(), kind,
                             cache='max-age=86400' if parts[0] == 'vendor' else 'no-cache')
        if path == '/openapi.json':
            return self.send(200, (ROOT / 'openapi.json').read_bytes())
        self.send(405 if path == '/mcp' else 404, {'error': 'Use POST /mcp.' if path == '/mcp' else 'Not found.'})

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if self.refused(write=True):
            return
        path = urlsplit(self.path).path
        try:
            body = self.body()
            if path == '/mcp':
                if not isinstance(body, dict):
                    return self.send(400, {'error': 'Expected a JSON-RPC object.'})
                out = rpc(body)
                return self.send(202 if out is None else 200, out)
            if not isinstance(body, dict):
                return self.send(400, {'error': 'Expected a JSON object.'})
            if path == '/api/places':
                return self.send(201, maps.save_place(body))
            m = re.fullmatch(r'/api/places/([A-Za-z0-9-]{6,64})', path)
            if m:
                return self.send(200, maps.update_place(m.group(1), body))
            m = re.fullmatch(r'/api/places/([A-Za-z0-9-]{6,64})/delete', path)
            if m:
                return self.send(200, maps.delete_place(m.group(1)))
            if path == '/api/lists':
                return self.send(201, maps.add_list(body))
            if path == '/api/route':
                return self.send(200, maps.route(body.get('from'), body.get('to'), body.get('mode'), body.get('depart_at'),
                                                 body.get('lang') or 'en'))
            if path == '/api/regions':
                return self.send(202, maps.add_region(body.get('id')))
            m = re.fullmatch(r'/api/regions/([a-z0-9-]{2,80})/(refresh|remove)', path)
            if m:
                fn = maps.refresh_region if m.group(2) == 'refresh' else maps.remove_region
                return self.send(202, fn(m.group(1)))
            if path == '/api/packs':
                return self.send(202, maps.make_pack(body))
            self.send(404, {'error': 'Not found.'})
        except Problem as e:
            self.send(e.code, {'error': e.message})
        except (ValueError, TypeError, AttributeError):
            self.send(400, {'error': 'Invalid JSON request.'})
        except Exception as e:  # noqa: BLE001
            self.send(502, {'error': f'Maps could not do that: {e}'})

    def log_message(self, format, *args):
        pass


# ---- wizard.app-switches/1 ------------------------------------------------------------------------------------------
# Owner feature switches for the Tracker's Try it (wizard-os docs/APP_SWITCHES.md). Shared block: the same text ships in
# every Wizard app; `wam new` starts every app with it (wizard-os AGENTS.md: a new app repo ships it in its first PR). Every new feature ships behind a switch, off by
# default: append it to SWITCHES with the issue it delivers, e.g.
#   {'id': 'compact-header', 'name': 'Compact top bar', 'description': '...', 'default': False,
#    'issues': ['humanitylabs-org/<repo>#20']}
# and gate the feature on switch_on('compact-header'). State lives on the box (DATA_DIR/switches.json), so the app's
# page, the Tracker (through the App Manager) and the agent see one state. GET/POST /api/switches, owner only, with
# X-Wizard-Switch: 1 and never a bearer or agent grant. An app with a platform owner-proof key (owner_proof in wizard.md,
# wizard-os spec/OWNER_PROOF.md) accepts only a valid signed proof: any process on the box can set the other headers.
# An app without one accepts the box's Tailscale Serve owner login, never a box-local host.
import hashlib as _sw_hashlib
import hmac as _sw_hmac
import json as _sw_json
import os as _sw_os
import re as _sw_re
import threading as _sw_threading
import time as _sw_time
from pathlib import Path as _SwPath
from urllib.parse import urlsplit as _sw_urlsplit

SWITCHES = []
_SW_CONTRACT = 'wizard.app-switches/1'
_SW_ROUTE = _sw_re.compile(r'(?:/[a-z0-9][a-z0-9-]{0,62})?/api/switches/?')
_SW_LOCAL = {'localhost', '127.0.0.1', '::1', '[::1]', '0.0.0.0'}
_sw_lock = _sw_threading.Lock()
_sw_nonces = {}
_SW_PROOF = _sw_re.compile(r'(\d{1,12})\.([0-9a-f]{48})\.([0-9a-f]{64})')


def _sw_file():
    return _SwPath(_sw_os.environ.get('WIZARD_SWITCHES_DIR') or DATA) / 'switches.json'


def _sw_saved():
    try:
        saved = _sw_json.loads(_sw_file().read_text())
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def switches_doc():
    saved = _sw_saved()
    return {'contract': _SW_CONTRACT, 'switches': [
        dict(s, on=saved[s['id']] if isinstance(saved.get(s['id']), bool) else bool(s.get('default'))) for s in SWITCHES]}


def switch_on(switch_id):
    return next((s['on'] for s in switches_doc()['switches'] if s['id'] == switch_id), False)


def _sw_set(switch_id, on):
    with _sw_lock:
        ids = {s['id'] for s in SWITCHES}
        saved = {k: v for k, v in _sw_saved().items() if k in ids and isinstance(v, bool)}
        saved[switch_id] = on
        f = _sw_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + '.tmp')
        tmp.write_text(_sw_json.dumps(saved))
        _sw_os.replace(tmp, f)
    return switches_doc()


def _sw_proof(h):
    """None when this app has no platform owner-proof key, else whether the request carries a valid, unused proof."""
    key_file = _sw_os.environ.get('WIZARD_OWNER_PROOF_KEY_FILE')
    if not key_file:
        return None
    try:
        with open(key_file, 'rb') as f:
            key = f.read(33)
    except OSError:
        return False                    # a key is named but unreadable: refuse, never fall back to headers
    app = _sw_os.environ.get('WIZARD_OWNER_PROOF_APP') or ''
    values = h.headers.get_all('X-Wizard-App-Owner-Proof') or []
    m = _SW_PROOF.fullmatch(values[0].strip()) if len(values) == 1 else None
    if len(key) != 32 or not app or not m or abs(_sw_time.time() - int(m.group(1))) > 30:
        return False
    msg = '\n'.join(('wizard-app-owner-v1', app, m.group(1), m.group(2), h.command, h.path)).encode()
    if not _sw_hmac.compare_digest(_sw_hmac.new(key, msg, _sw_hashlib.sha256).hexdigest(), m.group(3)):
        return False
    now = _sw_time.monotonic()
    with _sw_lock:
        for n in [n for n, until in _sw_nonces.items() if until < now]:
            del _sw_nonces[n]
        if m.group(2) in _sw_nonces:
            return False
        _sw_nonces[m.group(2)] = now + 60
    return True


def _sw_owner(h, write):
    hd = h.headers
    if hd.get('Authorization') or hd.get('X-Wizard-Switch') != '1':
        return False
    proof = _sw_proof(h)
    if proof is not None:
        return proof
    if (hd.get('Sec-Fetch-Site') or 'same-origin').lower() not in ('same-origin', 'none'):
        return False
    host = (hd.get('Host') or '').strip().lower()
    name = host.split(']')[0] + ']' if host.startswith('[') else host.rsplit(':', 1)[0]
    if not name or name in _SW_LOCAL or '.' not in name:      # box-local and container hosts never prove the owner
        return False
    origin = hd.get('Origin')
    trusted = {o.strip() for o in (_sw_os.environ.get('WIZARD_TRUSTED_ORIGINS') or '').split(',') if o.strip()}
    if origin and _sw_urlsplit(origin).netloc.lower() != host and origin not in trusted:
        return False
    if write and not origin:
        return False
    logins = [x.strip() for x in (hd.get_all('Tailscale-User-Login') or [])]
    owners = {x.strip().lower() for x in (_sw_os.environ.get('WIZARD_OWNER_LOGINS') or '').split(',') if x.strip()}
    return len(logins) == 1 and '@' in logins[0] and (not owners or logins[0].lower() in owners)


def _sw_send(h, code, doc):
    body = _sw_json.dumps(doc).encode()
    h.send_response(code)
    h.send_header('Content-Type', 'application/json')
    h.send_header('Cache-Control', 'no-store')
    h.send_header('Content-Length', str(len(body)))
    h.end_headers()
    if h.command != 'HEAD':
        h.wfile.write(body)


def _sw_handle(h):
    """Serve /api/switches (or /<app>/api/switches) and return True; False for every other request."""
    if not _SW_ROUTE.fullmatch(_sw_urlsplit(h.path).path):
        return False
    if h.command in ('GET', 'HEAD'):
        if not _sw_owner(h, False):
            _sw_send(h, 403, {'error': "Only this Wizard's owner can see these switches.", 'code': 'owner_only'})
        else:
            _sw_send(h, 200, switches_doc())
        return True
    if not _sw_owner(h, True):
        _sw_send(h, 403, {'error': "Only this Wizard's owner can change these switches.", 'code': 'owner_only'})
        return True
    try:
        if (h.headers.get('Content-Type') or '').split(';')[0].strip() != 'application/json':
            raise ValueError
        size = int(h.headers.get('Content-Length') or '0')
        if size <= 0 or size > 4096:
            raise ValueError
        body = _sw_json.loads(h.rfile.read(size))
        if not isinstance(body, dict) or not isinstance(body.get('id'), str) or not isinstance(body.get('on'), bool) \
                or set(body) - {'id', 'on'}:
            raise ValueError
    except (ValueError, TypeError):
        _sw_send(h, 400, {'error': 'Send JSON {"id": "...", "on": true|false}', 'code': 'invalid_input'})
        return True
    if body['id'] not in {s['id'] for s in SWITCHES}:
        _sw_send(h, 404, {'error': 'No such switch', 'code': 'unknown_switch'})
        return True
    try:
        _sw_send(h, 200, _sw_set(body['id'], body['on']))
    except OSError:
        _sw_send(h, 500, {'error': 'storage_failure'})
    return True


def wizard_switches_install(handler_cls):
    """Route the switch endpoint ahead of the app's own GET/HEAD/POST handlers."""
    for method in ('do_GET', 'do_HEAD', 'do_POST'):
        orig = getattr(handler_cls, method, None)
        if getattr(orig, '_wizard_switches', False):
            continue

        def wrapped(self, _orig=orig):
            if _sw_handle(self):
                return None
            if _orig is None:
                return self.send_error(501)
            return _orig(self)
        wrapped._wizard_switches = True
        setattr(handler_cls, method, wrapped)
    return handler_cls
# ---- end wizard.app-switches/1 ---------------------------------------------------------------------------------------


wizard_switches_install(Handler)



if __name__ == '__main__':
    maps.connect().close()
    if os.environ.get('MAPS_WORKER', '1') != '0':
        maps.WORKER.start()
    ThreadingHTTPServer((os.environ.get('BIND', '0.0.0.0'), int(os.environ.get('PORT', '8799'))), Handler).serve_forever()
