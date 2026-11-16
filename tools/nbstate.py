#!/usr/bin/env python3
"""Read/watch State or deliver an activation without Notchboard."""
import argparse
import json
import gi
gi.require_version('Gio','2.0')
from gi.repository import Gio, GLib

NAME = 'dev.kanterlabs.NotchboardActions'
PATH = '/dev/kanterlabs/NotchboardActions'
IFACE = 'dev.kanterlabs.Notchboard.Extension1'
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--watch',action='store_true')
p.add_argument('--activate',nargs='+',metavar='ID_OR_JSON')
a = p.parse_args()
bus = Gio.bus_get_sync(Gio.BusType.SESSION,None)

def show(value):
    print(json.dumps(json.loads(value),indent=2),flush=True)

if a.activate:
    if len(a.activate) > 2: p.error('expected ID [JSON]')
    value = a.activate[1] if len(a.activate) == 2 else 'null'
    json.loads(value)
    bus.call_sync(NAME,PATH,IFACE,'Activate',GLib.Variant('(ss)',(a.activate[0],value)),None,Gio.DBusCallFlags.NONE,5000,None)
if a.watch:
    def changed(conn,sender,path,iface,signal,params):
        _,values,_ = params.unpack()
        if 'State' in values: show(values['State'])
    bus.signal_subscribe(NAME,'org.freedesktop.DBus.Properties','PropertiesChanged',PATH,IFACE,Gio.DBusSignalFlags.NONE,changed)
result = bus.call_sync(NAME,PATH,'org.freedesktop.DBus.Properties','Get',GLib.Variant('(ss)',(IFACE,'State')),None,Gio.DBusCallFlags.NONE,5000,None)
show(result.unpack()[0])
if a.watch:
    try: GLib.MainLoop().run()
    except KeyboardInterrupt: pass
