#!/usr/bin/env python3
"""Phone dashboard for paper trading. Runs on the Mac; open it on a phone on the same Wi-Fi.

  python3 dashboard.py            # prints the address (with its access key) to open on the phone

Start/stop paper trading, save data, see open trades, today's log and the journal.
A random access key (saved in .dashboard_key) keeps other people on the Wi-Fi out.
Paper only: nothing here can place a real order.
"""
import csv
import datetime
import http.server
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("NSE_DASH_PORT", "8765"))
KEY_FILE = os.path.join(HERE, ".dashboard_key")
PID_FILE = os.path.join(HERE, "logs", "paper.pid")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def key():
    if not os.path.exists(KEY_FILE):
        with open(KEY_FILE, "w") as f:
            f.write(secrets.token_urlsafe(12))
        os.chmod(KEY_FILE, 0o600)
    return open(KEY_FILE).read().strip()


def today():
    return datetime.datetime.now(IST).date().isoformat()


def running_pid():
    try:
        pid = int(open(PID_FILE).read())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def launchd_running():
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if line.endswith("com.nse-day-trader.paper") and line.split()[0] != "-":
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def start_paper():
    if running_pid() or launchd_running():
        return "Already running."
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    log = open(os.path.join(HERE, "logs", "paper-%s.log" % today()), "a")
    p = subprocess.Popen(["/usr/bin/caffeinate", "-i", sys.executable, "-u", os.path.join(HERE, "trade.py"), "paper"],
                         cwd=HERE, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    with open(PID_FILE, "w") as f:
        f.write(str(p.pid))
    return "Started paper trading."


def stop_paper():
    pid = running_pid()
    if not pid:
        return "Not running (from this dashboard)."
    os.killpg(os.getpgid(pid), signal.SIGTERM)
    return "Stopped. Open paper positions stay in today's state and resume on the next start."


def collect():
    log = open(os.path.join(HERE, "logs", "collect-%s.log" % today()), "a")
    subprocess.Popen(["/bin/bash", os.path.join(HERE, "run_collect.sh")], cwd=HERE, stdout=log,
                     stderr=subprocess.STDOUT, start_new_session=True)
    return "Saving data in the background (about a minute)."


def status():
    st = {}
    p = os.path.join(HERE, "paper", "state-%s.json" % today())
    if os.path.exists(p):
        st = json.load(open(p))
    closed = st.get("closed", [])
    log = os.path.join(HERE, "logs", "paper-%s.log" % today())
    lines = open(log).read().splitlines()[-40:] if os.path.exists(log) else []
    journal = []
    jp = os.path.join(HERE, "paper", "journal.csv")
    if os.path.exists(jp):
        journal = [r for r in csv.DictReader(open(jp)) if r.get("mode") == "live"]
    nets = [float(r["net"]) for r in journal]
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    return {
        "now": datetime.datetime.now(IST).strftime("%a %d %b, %H:%M IST"),
        "running": bool(running_pid() or launchd_running()),
        "strategies": cfg.get("strategies"),
        "open": st.get("open", {}),
        "closed": closed,
        "today_net": round(sum(t["net"] for t in closed), 0),
        "log": lines,
        "all_trades": len(nets),
        "all_wins": sum(x > 0 for x in nets),
        "all_net": round(sum(nets), 0),
        "recent": journal[-15:][::-1],
    }


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NSE Paper Trader</title><style>
:root{color-scheme:light dark;--bg:#f6f6f4;--card:#fff;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e2dd;--g:#1a7f37;--r:#c22d2d;--b:#1f5fbf}
@media(prefers-color-scheme:dark){:root{--bg:#121211;--card:#1d1d1b;--fg:#ececea;--mut:#9a9a94;--line:#2e2e2b;--g:#3fb950;--r:#f0605d;--b:#5b9cf5}}
*{box-sizing:border-box}body{margin:0;font:15px/1.4 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg);padding:14px 14px 40px;max-width:560px;margin:auto}
h1{font-size:19px;margin:4px 0 2px}.mut{color:var(--mut);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:12px 0}
.row{display:flex;gap:8px;flex-wrap:wrap}button{flex:1;min-width:30%;font:600 15px system-ui;padding:13px 8px;border-radius:11px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
button.go{background:var(--g);color:#fff;border:0}button.stop{background:var(--r);color:#fff;border:0}
.big{font-size:28px;font-weight:700}.pos{color:var(--g)}.neg{color:var(--r)}
.pill{display:inline-block;padding:3px 10px;border-radius:99px;font-size:13px;font-weight:600}
.on{background:color-mix(in srgb,var(--g) 18%,transparent);color:var(--g)}.off{background:color-mix(in srgb,var(--mut) 18%,transparent);color:var(--mut)}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:6px 4px;border-bottom:1px solid var(--line);text-align:left}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
pre{white-space:pre-wrap;font:12px/1.45 ui-monospace,Menlo,monospace;margin:0;max-height:300px;overflow:auto}
#msg{min-height:20px;font-size:13px;color:var(--b);margin-top:8px}</style></head><body>
<h1>NSE Paper Trader</h1><div class="mut" id="now"></div>
<div class="card"><div style="display:flex;justify-content:space-between;align-items:center">
<span id="state" class="pill off">...</span><span class="mut" id="strat"></span></div>
<div class="mut" style="margin-top:10px">Today, after charges</div><div class="big" id="today">Rs 0</div>
<div class="row" style="margin-top:12px"><button class="go" onclick="act('start')">Start</button>
<button class="stop" onclick="act('stop')">Stop</button><button onclick="act('collect')">Save data</button></div>
<div id="msg"></div></div>
<div class="card"><b>Open positions</b><table id="openT"></table></div>
<div class="card"><b>Closed today</b><table id="closedT"></table></div>
<div class="card"><b>All paper trades</b><div class="mut" id="tot"></div><table id="recent"></table></div>
<div class="card"><b>Live log</b><pre id="log"></pre></div>
<p class="mut">Dummy money only. No real orders are ever sent.</p>
<script>
const K=new URLSearchParams(location.search).get('k')||'';
const rs=x=>(x>0?'+':'')+'Rs '+Math.round(x).toLocaleString('en-IN');
const cls=x=>x>0?'pos':(x<0?'neg':'');
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function load(){const r=await fetch('api/status?k='+K);if(!r.ok){document.body.innerHTML='<p>Access key missing or wrong. Open the full link printed on the Mac.</p>';return}
const d=await r.json();now.textContent=d.now;state.textContent=d.running?'Running':'Stopped';state.className='pill '+(d.running?'on':'off');
strat.textContent=(d.strategies||[]).join(', ');today.textContent=rs(d.today_net);today.className='big '+cls(d.today_net);
const o=Object.entries(d.open);openT.innerHTML=o.length?'<tr><th>Stock</th><th>Side</th><th class=n>Qty</th><th class=n>Entry</th><th class=n>Stop</th><th class=n>Target</th></tr>'+o.map(([s,p])=>`<tr><td>${esc(s)}</td><td>${p.side>0?'BUY':'SHORT'}</td><td class=n>${p.qty}</td><td class=n>${p.entry}</td><td class=n>${p.stop}</td><td class=n>${p.target}</td></tr>`).join(''):'<tr><td class=mut>None</td></tr>';
closedT.innerHTML=d.closed.length?'<tr><th>Stock</th><th>Exit</th><th class=n>Net</th></tr>'+d.closed.map(t=>`<tr><td>${esc(t.symbol)} ${t.side>0?'BUY':'SHORT'}</td><td>${esc(t.reason)} ${t.exit_time}</td><td class="n ${cls(t.net)}">${rs(t.net)}</td></tr>`).join(''):'<tr><td class=mut>None yet</td></tr>';
tot.textContent=d.all_trades?`${d.all_trades} trades, ${d.all_wins} won, total ${rs(d.all_net)}`+(d.all_trades<100?' (under 100 trades: too few to judge)':''):'No live paper trades yet.';
recent.innerHTML=d.recent.map(t=>`<tr><td>${t.date.slice(5)}</td><td>${esc(t.symbol)}</td><td>${esc(t.reason)}</td><td class="n ${cls(+t.net)}">${rs(+t.net)}</td></tr>`).join('');
log.textContent=d.log.join('\\n')||'Nothing yet today.';log.scrollTop=1e9}
async function act(a){if(a==='stop'&&!confirm('Stop paper trading?'))return;msg.textContent='...';
const r=await fetch('api/'+a+'?k='+K,{method:'POST'});msg.textContent=(await r.json()).msg;setTimeout(load,800)}
load();setInterval(load,15000);
</script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def _ok(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return secrets.compare_digest((q.get("k") or [""])[0], key())

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._ok():
            return self._send(403, "Forbidden: open the full link (with ?k=...) printed on the Mac.", "text/plain")
        if path == "/":
            return self._send(200, PAGE, "text/html")
        if path == "/api/status":
            return self._send(200, json.dumps(status()))
        self._send(404, "{}")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._ok():
            return self._send(403, "{}")
        fn = {"/api/start": start_paper, "/api/stop": stop_paper, "/api/collect": collect}.get(path)
        if not fn:
            return self._send(404, "{}")
        try:
            msg = fn()
        except Exception as e:  # show the reason on the phone instead of a blank failure
            msg = "Error: %s" % e
        self._send(200, json.dumps({"msg": msg}))

    def log_message(self, fmt, *args):
        pass


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    k = key()
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print("NSE paper-trading dashboard running. On your phone (same Wi-Fi), open:\n")
    print("    http://%s:%d/?k=%s\n" % (lan_ip(), PORT, k))
    print("Keep this window open. Ctrl-C stops the dashboard (not a running paper session).")
    sys.stdout.flush()
    srv.serve_forever()
