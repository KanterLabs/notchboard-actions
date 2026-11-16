"""Read-only API clients. Credentials and response caches stay in memory."""
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import urlsplit, quote
from runs import normalize, stamp


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward credentials to another host.


def gh_token():
    if not shutil.which('gh'):
        return None
    try:
        p = subprocess.run(['gh','auth','token'], capture_output=True, text=True, timeout=10)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def keyring_token(url):
    import gi
    gi.require_version('Secret','1')
    from gi.repository import Secret
    schema = Secret.Schema.new('dev.kanterlabs.NotchboardActions', Secret.SchemaFlags.DONT_MATCH_NAME,
                               {'service':Secret.SchemaAttributeType.STRING,'host':Secret.SchemaAttributeType.STRING})
    try:
        return Secret.password_lookup_sync(schema, {'service':'dev.kanterlabs.NotchboardActions',
                                                   'host':urlsplit(url).hostname}, None)
    except Exception:
        return None


def config():
    path = os.path.join(os.environ.get('XDG_CONFIG_HOME',os.path.expanduser('~/.config')), 'notchboard-actions','config.json')
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


class HTTP:
    def __init__(self, token, github=False):
        self.token, self.github, self.cache = token, github, {}

    def get(self, url):
        headers = {'Accept':'application/json', 'User-Agent':'notchboard-actions'}
        if self.token:
            headers['Authorization'] = ('Bearer ' if self.github else 'token ') + self.token
        if self.github:
            headers['X-GitHub-Api-Version'] = '2022-11-28'
        if url in self.cache and self.cache[url][0]:
            headers['If-None-Match'] = self.cache[url][0]
        for attempt in range(2):
            try:
                with build_opener(NoRedirect()).open(Request(url, headers=headers, method='GET'), timeout=10) as response:
                    data = json.load(response)
                    self.cache[url] = (response.headers.get('ETag'), data)
                    return data
            except HTTPError as e:
                if e.code == 304 and url in self.cache:
                    return self.cache[url][1]
                if e.code == 401 and self.github and attempt == 0:
                    self.last_lookup = time.time()
                    self.token = gh_token()
                    if self.token:
                        headers['Authorization'] = 'Bearer ' + self.token
                        continue
                raise RuntimeError('API request failed') from None


class EndpointClient:
    def credentials(self, lookup):
        now = time.time()
        if not self.http.token and now-max(getattr(self, 'token_checked', float('-inf')), getattr(self.http, 'last_lookup', float('-inf'))) >= 60:
            self.token_checked = now
            self.http.token = lookup()
        if not self.http.token:
            raise RuntimeError('Credentials unavailable')

    def endpoints(self, endpoints, source):
        now = time.time()
        if not hasattr(self, 'snapshots'):
            self.snapshots = {}
        self.errors = []
        active = set()
        for endpoint, url in endpoints:
            active.add(endpoint)
            try:
                data = self.http.get(url)
                rows = [normalize(source, r) for r in data.get('workflow_runs') or []]
                self.snapshots[endpoint] = (now, rows)
            except Exception:
                self.errors.append(source)
        self.snapshots = {k:v for k,v in self.snapshots.items() if k in active}
        combined = {}
        for fetched, rows in self.snapshots.values():
            for r in rows:
                if r.status in ('run','queued') and now-fetched > 300:
                    continue
                if r.key not in combined or r.attempt >= combined[r.key].attempt:
                    combined[r.key] = r
        return list(combined.values())


class GitHub(EndpointClient):
    def __init__(self, cfg):
        self.cfg, self.http = cfg, HTTP(gh_token(), True)
        self.token_checked = time.time()
        self.repos, self.refreshed = [], float('-inf')

    def poll(self):
        if not self.cfg.get('enabled',True):
            return []
        self.credentials(gh_token)
        now = time.time()
        if 'repos' in self.cfg:
            repos = self.cfg['repos']
        else:
            if now-self.refreshed >= 600:
                try:
                    data = self.http.get('https://api.github.com/user/repos?sort=pushed&per_page=30')
                    self.repos = [r['full_name'] for r in data if now-stamp(r.get('pushed_at')) <= self.cfg.get('pushed_within_days',14)*86400]
                    self.refreshed = now
                except Exception:
                    discovery_error = True
            repos = self.repos
        endpoints = []
        for repo in repos:
            path = '/'.join(quote(p,safe='') for p in repo.split('/'))
            endpoints.append((repo, f'https://api.github.com/repos/{path}/actions/runs?per_page=5'))
        rows = self.endpoints(endpoints, 'github')
        if locals().get('discovery_error'):
            self.errors.append('github')
        return rows


class Gitea(EndpointClient):
    def __init__(self,cfg):
        self.cfg = cfg
        self.url = cfg.get('url','').rstrip('/')
        parsed = urlsplit(self.url)
        if self.url and (parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('Invalid Gitea URL')
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Gitea requires HTTPS except on localhost')
        self.http = HTTP(keyring_token(self.url) if self.url else None)
        self.token_checked = time.time()

    def poll(self):
        if not self.url:
            return []
        self.credentials(lambda: keyring_token(self.url))
        paths = ['user'] + ['orgs/'+quote(org,safe='') for org in self.cfg.get('orgs',[])]
        return self.endpoints([(p, f'{self.url}/api/v1/{p}/actions/runs?limit=20') for p in paths], 'gitea')


class Sources:
    def __init__(self,cfg=None):
        cfg = config() if cfg is None else cfg
        self.clients, self.config_errors = {}, []
        for name, client in (('github',GitHub), ('gitea',Gitea)):
            try:
                self.clients[name] = client(cfg.get(name,{}))
            except Exception:
                self.config_errors.append(name)
        self.schedule, self.snapshots, self.last_success = {}, {}, {}
        self.last = {}
        self.links = {} if cfg.get('github',{}).get('enabled') is False else {'github':'https://github.com/actions'}
        if cfg.get('gitea',{}).get('url'):
            self.links['gitea'] = cfg['gitea']['url'].rstrip('/') + '/-/actions'

    def poll(self, panel=False, force=False):
        now = time.time()
        errors = list(self.config_errors)
        if panel:
            for s,(deadline,count) in self.schedule.items():
                if not count:
                    self.schedule[s] = (min(deadline,self.last_success.get(s,0)+5),count)
        due = {s:c for s,c in self.clients.items() if force or now >= self.schedule.get(s, (0,0))[0]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {s:pool.submit(c.poll) for s,c in due.items()}
            for source,future in futures.items():
                failed = False
                try:
                    self.last[source] = future.result()
                    self.last_success[source] = now
                    failed = bool(getattr(self.clients[source], 'errors', []))
                    for endpoint, (fetched, rows) in getattr(self.clients[source], 'snapshots', {}).items():
                        if source == 'github':
                            self.snapshots[(source, endpoint)] = fetched
                        else:
                            self.snapshots[(source, '*')] = max(fetched, self.snapshots.get((source, '*'), 0))
                        for r in rows:
                            if source == 'github':
                                key = (r.source,r.repo)
                                self.snapshots[key] = max(fetched, self.snapshots.get(key, 0))
                except Exception:
                    failed = True
                count = self.schedule.get(source, (0,0))[1]+1 if failed else 0
                active = any(r.status in ('run','queued') for r in self.last.get(source, []))
                interval = 5 if panel else (15 if active else 60)
                if failed:
                    interval = min(300, interval * 2**min(count,6))
                self.schedule[source] = (time.time()+interval, count)
        errors.extend(s for s,(_,count) in self.schedule.items() if count)
        rows = [r for source, runs in self.last.items() for r in runs
                if r.status not in ('run','queued') or now-self.last_success.get(source,0) <= 300]
        # Endpoint-level expiry also applies while a source is backing off.
        valid = {r.key for c in self.clients.values() for fetched, runs in getattr(c, 'snapshots', {}).values()
                 for r in runs if r.status not in ('run','queued') or now-fetched <= 300}
        rows = [r for r in rows if r.status not in ('run','queued') or r.key in valid or
                not getattr(self.clients.get(r.source), 'snapshots', {})]
        return rows, list(dict.fromkeys(errors))
