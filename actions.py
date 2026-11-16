#!/usr/bin/env python3
"""Notchboard Actions session-bus service."""
import argparse
import json
import signal
import sys
import threading
import time
import gi
gi.require_version('Gio','2.0')
from gi.repository import Gio, GLib
from runs import Model, Run, validate
from sources import Sources

NAME = 'dev.kanterlabs.NotchboardActions'
PATH = '/dev/kanterlabs/NotchboardActions'
IFACE = 'dev.kanterlabs.Notchboard.Extension1'
XML = f'''<node><interface name="{IFACE}">
<property name="State" type="s" access="read"/>
<method name="Activate"><arg type="s" name="id" direction="in"/>
<arg type="s" name="value" direction="in"/></method></interface></node>'''


def demo_runs(start):
    phase = int((time.time()-start)//4) % 5
    now = time.time()
    status = ['queued','run','run','low','good'][phase]
    return [Run('github','1','my-org/example','Tests','main',status,start,
                now if status in ('good','low') else 0,'https://github.com/my-org/example/actions/runs/1','tests')]


class Service:
    def __init__(self,demo=False):
        self.demo, self.start = demo, time.time()
        self.sources = None
        self.model = Model({'github':'https://github.com/actions'})
        self.state = json.dumps(self.model.state(time.time()))
        self.bus = None
        self.busy, self.panel, self.pending = False, False, False
        self.next_poll = 0
        self.loop = GLib.MainLoop()

    def publish(self,state):
        try:
            validate(state)
            encoded = json.dumps(state)
        except Exception:
            print('State validation failed', file=sys.stderr)
            encoded = getattr(self, 'state', '{}')
        if encoded != self.state:
            self.state = encoded
            if self.bus:
                self.bus.emit_signal(None,PATH,'org.freedesktop.DBus.Properties','PropertiesChanged',
                                     GLib.Variant('(sa{sv}as)',(IFACE,{'State':GLib.Variant('s',encoded)},[])))

    def render(self, now, rows=None):
        if not self.panel:
            now = int(now // 5) * 5
        try:
            if rows is None:
                state = self.model.state(now)
            else:
                state = self.model.ingest(rows, now, self.sources.snapshots if self.sources else None)
        except Exception:
            print('State construction failed', file=sys.stderr)
            return
        self.publish(state)

    def activate(self,conn,sender,path,iface,method,params,invocation):
        id,value = params.unpack()
        try:
            value = json.loads(value)
        except ValueError:
            value = None
        if id == 'refresh':
            self.next_poll = 0
            self.pending = self.busy
        elif id == 'notchboard.panel' and isinstance(value,bool):
            self.panel = value
            if value:
                self.model.panel_open()
                self.next_poll = 0
                self.pending = self.busy
        elif id == 'notchboard.offer' and isinstance(value,dict):
            card = self.model.offers.current
            if card and value.get('id') == card['id'] and value.get('result') in ('dismissed','opened','expired','replaced','suppressed'):
                self.model.offers.current = None
        self.render(time.time())
        invocation.return_value(None)

    def acquired(self,bus,name):
        self.bus = bus
        register = bus.register_object_with_closures2 if hasattr(bus, 'register_object_with_closures2') else bus.register_object
        self.registration = register(PATH,Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],self.activate,
                                                lambda *args:GLib.Variant('s',self.state),None)
        GLib.timeout_add(1000,self.tick)
        self.tick()

    def worker(self):
        try:
            if self.demo:
                rows,errors = demo_runs(self.start),[]
            else:
                if self.sources is None:
                    self.sources = Sources()
                rows,errors = self.sources.poll(self.panel, self.pending or self.next_poll == 0)
            GLib.idle_add(self.received,rows,errors)
        except Exception:
            GLib.idle_add(self.received,None,['configuration'])

    def received(self,rows,errors):
        self.busy = False
        now = time.time()
        if self.sources:
            self.model.links = self.sources.links
        self.model.errors = errors
        if rows is not None:
            self.render(now, rows)
        self.next_poll = 0 if self.pending else now+(2 if self.demo else 1)
        self.pending = False
        return False

    def tick(self):
        now = time.time()
        # Quantize elapsed-only changes while closed; worker results bypass this.
        if self.panel or now-getattr(self, 'last_clock', 0) >= 5:
            self.render(now)
            self.last_clock = now
        if not self.busy and now >= self.next_poll:
            self.busy = True
            threading.Thread(target=self.worker,daemon=True).start()
        return True

    def run(self):
        owner = Gio.bus_own_name(Gio.BusType.SESSION,NAME,Gio.BusNameOwnerFlags.NONE,self.acquired,None,lambda *a:self.loop.quit())
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,signal.SIGTERM,lambda:self.loop.quit())
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,signal.SIGINT,lambda:self.loop.quit())
        try: self.loop.run()
        finally: Gio.bus_unown_name(owner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--demo',action='store_true')
    args = parser.parse_args()
    if args.once:
        model = Model()
        if args.demo:
            rows,errors = demo_runs(time.time()),[]
        else:
            try:
                sources = Sources()
                rows,errors = sources.poll()
                model.links = sources.links
            except Exception:
                rows,errors = [],['configuration']
        model.errors = errors
        print(json.dumps(model.ingest(rows,time.time())))
        return bool(errors)
    Service(args.demo).run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
