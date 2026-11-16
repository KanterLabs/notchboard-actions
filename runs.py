"""Pure run model, lifecycle and contract-v1 presentation."""
from dataclasses import dataclass
from datetime import datetime
import json
import time
from urllib.parse import urlsplit


def stamp(value):
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0.0


def uri(value):
    return value if isinstance(value, str) and urlsplit(value).scheme in ('http', 'https') else ''


@dataclass(frozen=True)
class Run:
    source: str
    id: str
    repo: str
    workflow: str
    branch: str
    status: str
    started: float
    ended: float
    url: str
    workflow_id: str = ''
    attempt: int = 1
    created: float = 0

    @property
    def key(self):
        return (self.source, self.id)

    @property
    def duration(self):
        return max(0, self.ended - self.started) if self.started else 0


def normalize(source, raw):
    status = raw.get('conclusion') or raw.get('status', '')
    status = {'in_progress':'run', 'running':'run', 'queued':'queued',
              'waiting':'queued', 'pending':'queued', 'requested':'queued',
              'success':'good', 'failure':'low', 'failed':'low',
              'timed_out':'low', 'startup_failure':'low', 'blocked':'queued'}.get(status, 'done')
    repo = raw.get('repository') or {}
    filename = str(raw.get('path') or '').split('@')[0].rsplit('/', 1)[-1]
    display = filename.removesuffix('.yaml').removesuffix('.yml')
    return Run(source, str(raw['id']), str(repo.get('full_name') or repo.get('name') or 'repository'),
               str(raw.get('name') or raw.get('workflow_id') or display or 'workflow'),
               str(raw.get('head_branch') or ''), status,
               next((t for t in map(stamp, (raw.get('run_started_at'), raw.get('started_at'), raw.get('created_at'))) if t > 0), 0),
               (next((t for t in map(stamp, (raw.get('completed_at'), raw.get('updated_at'))) if t > 0), 0)
                if status not in ('run','queued') else 0), uri(raw.get('html_url', '')),
               str(raw.get('workflow_id') or filename or raw.get('name') or ''), int(raw.get('run_attempt') or 1), stamp(raw.get('created_at')))


def diff(prev, cur, now, since=None):
    events = []
    for r in cur.values():
        old = prev.get(r.key)
        fresh = old is None and (since is None or (r.ended or r.created or r.started) > since)
        retry = old is not None and r.attempt > old.attempt
        if r.status == 'run' and (fresh or retry or (old and old.status == 'queued')):
            events.append(('start', r))
        elif r.status in ('low', 'good') and (fresh or retry or (old and old.status in ('run', 'queued'))):
            if r.status == 'low' or r.duration > 120:
                events.append((r.status, r))
    return events


def clock(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}:{seconds % 60:02}'


def progress(r, now, history):
    if r.status == 'queued':
        return 'busy'
    duration = history.get((r.source, r.repo, r.workflow_id), 0)
    return min(.95, max(0, now-r.started)/duration) if duration else 'busy'


class Offers:
    def __init__(self):
        self.queue = []
        self.seen = {}
        self.last = float('-inf')
        self.current = None
        self.expires = 0

    def update(self, events, runs, now, history):
        self.seen = {k:t for k,t in self.seen.items() if now-t < 86400}
        if self.current and not any(self.current['id'].endswith(f':{r.source}:{r.id}:{r.attempt}') for r in runs.values()):
            self.current = None
        for kind, r in events:
            key = f'{kind}:{r.source}:{r.id}:{r.attempt}'
            if key not in self.seen:
                self.seen[key] = now
                self.queue.append((kind, r, key))
        self.queue = [e for e in self.queue if e[1].key in runs and runs[e[1].key].attempt == e[1].attempt and
                      (e[0] != 'start' or runs[e[1].key].status == 'run')]
        self.queue.sort(key=lambda e: {'low':0, 'good':1, 'start':2}[e[0]])
        if now >= self.expires:
            self.current = None
        if self.queue and now-self.last >= 10:
            kind, r, key = self.queue.pop(0)
            seconds = 10 if kind == 'low' else 6
            title = f'{r.repo} · {r.workflow}'
            text = f'{r.branch} · started'
            card = {'id':key, 'title':title, 'text':text, 'seconds':seconds}
            if r.url:
                card['uri'] = r.url
            if kind == 'start':
                card.update(ring=progress(r, now, history), progress=progress(r, now, history))
            else:
                card.update(title=title + (' failed' if kind == 'low' else ''), tone=kind,
                            text=f'{r.branch} · ' + (f'after {clock(r.duration)}' if kind == 'low' else f'passed in {clock(r.duration)}'))
            self.current, self.last, self.expires = card, now, now+seconds
        if self.current and self.current['id'].startswith('start:'):
            for r in runs.values():
                if self.current['id'] == f'start:{r.source}:{r.id}:{r.attempt}':
                    self.current.update(ring=progress(r, now, history), progress=progress(r, now, history))
        return self.current


def build_state(runs, events, now, *, offers=None, history=None, cleared=None, links=None, errors=()):
    history, cleared, links = history or {}, cleared or set(), links or {}
    visible = [r for r in runs.values() if r.status in ('run','queued') or
               (r.status == 'low' and r.key not in cleared and max(0, now-r.ended) < 600) or
               (r.status == 'good' and max(0, now-r.ended) < 30)]
    items = []
    for r in sorted((r for r in visible if r.status != 'good'), key=lambda r: ({'run':0,'queued':1,'low':2}[r.status], r.started))[:10]:
        row = {'type':'row', 'title':f'{r.repo} · {r.workflow}',
               'subtitle':f'{r.branch} · ' + ('queued' if r.status == 'queued' else clock(r.duration if r.status == 'low' else now-r.started)),
               'hint': 'GitHub' if r.source == 'github' else 'Gitea',
               'ring':progress(r, now, history), 'tone':'low' if r.status == 'low' else 'normal'}
        if r.url:
            row['uri'] = r.url
        items.append(row)
    if errors:
        items.append({'type':'text','text':'Some sources unavailable; showing last known runs','tone':'dim'})
    pills = [{'id':s, 'label':label, 'uri':uri(links[s])} for s, label in [('gitea','Gitea Actions'),('github','GitHub Actions')] if uri(links.get(s,''))]
    pills.append({'id':'refresh','label':'Refresh'})
    items.append({'type':'pills','pills':pills})
    state = {'panel':{'title':'Running actions','items':items}, 'actions':[
        {'id':p['id'],'name':p['label'] if p['id'] != 'refresh' else 'Refresh actions', **({'uri':p['uri']} if 'uri' in p else {})} for p in pills]}
    if visible:
        counts = [(sum(r.status == status for r in visible), label) for status,label in [('run','workflow runs in progress'),('queued','queued'),('low','failed'),('good','passed')]]
        state['chip'] = {'where':'notch','dots':[r.status for r in sorted(visible,key=lambda r:(r.status not in ('run','queued'), -r.started))[:8]],
                         'label':', '.join(f'{n} {label}' for n,label in counts if n)}
    if offers:
        card = offers.update(events, runs, now, history)
        if card:
            state['offer'] = dict(card)
    # Bound untrusted server strings, then trim rows to fit the byte budget.
    def trim(v):
        if isinstance(v,str): return v[:512]
        if isinstance(v,list): return [trim(x) for x in v]
        if isinstance(v,dict): return {k:trim(x) for k,x in v.items()}
        return v
    state = trim(state)
    while len(json.dumps(state).encode()) > 16384 and len(state['panel']['items']) > 1:
        state['panel']['items'].pop(0)
    validate(state)
    return state


def validate(state):
    def require(condition):
        if not condition:
            raise ValueError("Invalid State")
    require(len(json.dumps(state).encode()) <= 16384)
    chip = state.get('chip', {})
    require(len(chip.get('text','')) <= 12)
    if chip:
        require(chip.get('where') in ('notch','corner'))
        require(isinstance(chip.get('label'),str))
    require(chip.get('tone','normal') in ('normal','dim','good','low'))
    require(isinstance(chip.get('badge',0),int) and 0 <= chip.get('badge',0) <= 99)
    require('dots' not in chip or 1 <= len(chip['dots']) <= 8)
    require(all(d in ('run','queued','low','good') for d in chip.get('dots',[])))
    require(len(state.get('panel',{}).get('items',[])) <= 12)
    require(len(state.get('actions',[])) <= 8)
    def check(v):
        if isinstance(v,dict):
            for k,x in v.items():
                if k == 'uri': require(uri(x))
                if k in ('ring','progress'): require(x == 'busy' or isinstance(x,(float,int)) and 0 <= x <= 1)
                if k == 'pills': require(1 <= len(x) <= 3)
                check(x)
        elif isinstance(v,list):
            for x in v: check(x)
    check(state)
    if 'offer' in state:
        require(3 <= state['offer'].get('seconds',8) <= 30)
        require(len(state['offer'].get('actions',[])) <= 2)
    return True


class Model:
    def __init__(self, links=None):
        self.runs, self.history, self.cleared = {}, {}, {}
        self.polled = {}
        self.offers, self.links, self.errors = Offers(), links or {}, ()

    def ingest(self, runs, now, snapshots=None):
        current = {}
        for r in runs:
            if r.key not in current or r.attempt >= current[r.key].attempt:
                current[r.key] = r
        events = []
        if snapshots is None:
            snapshots = {(r.source, r.repo): now for r in runs}
        for endpoint, fetched in snapshots.items():
            previous = self.polled.get(endpoint, self.polled.get((endpoint[0], '*')))
            subset = {k:r for k,r in current.items() if (r.source, r.repo) == endpoint or (endpoint == (r.source, '*'))}
            if previous is not None and fetched > previous:
                events.extend(diff(self.runs, subset, now, previous))
            self.polled[endpoint] = fetched
        self.cleared = {k:t for k,t in self.cleared.items() if now-t < 86400}
        for k,r in current.items():
            if k in self.runs and r.attempt > self.runs[k].attempt:
                self.cleared.pop(k, None)
        # Retain completed runs through their linger even if absent from a later page.
        for key,r in self.runs.items():
            if key not in current and r.status in ('good','low') and now-r.ended < 600:
                current[key] = r
        for r in sorted(current.values(), key=lambda r:r.ended):
            if r.status == 'good' and r.duration:
                self.history[(r.source,r.repo,r.workflow_id)] = r.duration
        self.runs = current
        return self.state(now, events)

    def panel_open(self):
        self.cleared.update({r.key:time.time() for r in self.runs.values() if r.status == 'low'})

    def state(self, now, events=()):
        return build_state(self.runs, events, now, offers=self.offers, history=self.history,
                           cleared=self.cleared, links=self.links, errors=self.errors)
