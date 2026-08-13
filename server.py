#!/usr/bin/env python3
import http.server, socketserver, json, time, threading, subprocess, os, secrets

PORT = 8080
MAX_POINTS = 120
PEERS = {'B': '10.0.10.14', 'C': '120.131.13.144'}

# socket/TCP 状态采样参数（可被 config.json 覆盖）
NETSTAT_INTERVAL = 5    # 采样间隔（秒）
NETSTAT_POINTS = 2880   # 历史保留点数（5s × 2880 = 4h）

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
LOGIN_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'login.html')
INDEX_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')

TOKENS = {}
CREDENTIALS = {}

def load_config():
    global CREDENTIALS, NETSTAT_INTERVAL, NETSTAT_POINTS
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
            CREDENTIALS = {cfg['username']: cfg['password']}
            NETSTAT_INTERVAL = cfg.get('netstat_interval', NETSTAT_INTERVAL)
            NETSTAT_POINTS = cfg.get('netstat_points', NETSTAT_POINTS)
    except Exception as e:
        print(f'Warning: failed to load config.json: {e}')
        CREDENTIALS = {'admin': 'admin'}

history = []
prev = None
prev_ts = 0
lat_b = -1
lat_c = -1
netstat_history = []

def detect_iface():
    with open('/proc/net/dev') as f:
        for line in f.readlines()[2:]:
            p = line.strip().split(':')
            if len(p) == 2 and p[0].strip() != 'lo' and int(p[1].split()[0]) > 0:
                return p[0].strip()
    return 'eth0'

def read_dev(iface):
    with open('/proc/net/dev') as f:
        for line in f.readlines()[2:]:
            p = line.strip().split(':')
            if len(p) == 2 and p[0].strip() == iface:
                vals = p[1].split()
                return [int(x) for x in vals[:4] + vals[8:12]]
    return None

def ping(host):
    try:
        r = subprocess.run(['ping','-c','1','-W','1',host],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=3)
        for l in r.stdout.split('\n'):
            if 'rtt' in l and '=' in l:
                return float(l.split('=')[1].split('/')[1])
    except: pass
    return -1

def collect(iface):
    global prev, prev_ts, lat_b, lat_c
    while True:
        now = time.time()
        cur = read_dev(iface)
        if cur and prev:
            dt = now - prev_ts
            if dt > 0:
                pt = {
                    't': round(now, 1),
                    'bps_in': round((cur[0]-prev[0])*8/dt, 0),
                    'bps_out': round((cur[4]-prev[4])*8/dt, 0),
                    'pps_in': round((cur[1]-prev[1])/dt, 0),
                    'pps_out': round((cur[5]-prev[5])/dt, 0),
                    'drop_in': cur[3]-prev[3],
                    'drop_out': cur[7]-prev[7],
                    'err_in': cur[2]-prev[2],
                    'err_out': cur[6]-prev[6],
                    'lat_b': lat_b,
                    'lat_c': lat_c,
                }
                history.append(pt)
                if len(history) > MAX_POINTS:
                    del history[0]
        if cur:
            prev = cur
            prev_ts = now
        time.sleep(1)

def ping_loop():
    global lat_b, lat_c
    while True:
        lat_b = ping(PEERS['B'])
        lat_c = ping(PEERS['C'])
        time.sleep(3)

# ---- socket / TCP 状态采集 ----
# /proc/net/tcp[*] 第4列十六进制状态码 -> 名称
TCP_STATES = {
    '01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV',
    '04': 'FIN_WAIT1', '05': 'FIN_WAIT2', '06': 'TIME_WAIT',
    '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK',
    '0A': 'LISTEN', '0B': 'CLOSING', '0C': 'NEW_SYN_RECV',
}

def read_sockstat():
    s = {'total': 0, 'tcp_inuse': 0, 'tcp_orphan': 0, 'tcp_tw': 0, 'tcp_alloc': 0,
         'udp_inuse': 0, 'raw_inuse': 0, 'unix': 0}
    def parse_pairs(line):
        d = {}
        parts = line.split()
        for i in range(1, len(parts) - 1, 2):
            try:
                d[parts[i]] = int(parts[i + 1])
            except Exception:
                pass
        return d
    for fn in ('/proc/net/sockstat', '/proc/net/sockstat6'):
        try:
            with open(fn) as f:
                for line in f:
                    if line.startswith('sockets:'):
                        try:
                            s['total'] = int(line.split(':')[1].split()[1])
                        except Exception:
                            pass
                    elif line.startswith('TCP') and 'inuse' in line:
                        d = parse_pairs(line)
                        s['tcp_inuse'] += d.get('inuse', 0)
                        s['tcp_orphan'] += d.get('orphan', 0)
                        s['tcp_tw'] += d.get('tw', 0)
                        s['tcp_alloc'] += d.get('alloc', 0)
                    elif line.startswith('UDP') and 'inuse' in line:
                        s['udp_inuse'] += parse_pairs(line).get('inuse', 0)
                    elif line.startswith('RAW') and 'inuse' in line:
                        s['raw_inuse'] += parse_pairs(line).get('inuse', 0)
        except Exception:
            pass
    try:
        n = 0
        with open('/proc/net/unix') as f:
            for _ in f:
                n += 1
        s['unix'] = max(0, n - 1)
    except Exception:
        pass
    return s

def read_tcp_states():
    counts = {}
    for fn in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            with open(fn) as f:
                next(f, None)
                for line in f:
                    p = line.split()
                    if len(p) >= 4:
                        name = TCP_STATES.get(p[3], p[3])
                        counts[name] = counts.get(name, 0) + 1
        except Exception:
            pass
    return counts

def collect_netstat():
    global netstat_history
    while True:
        pt = {
            't': round(time.time(), 1),
            'sockstat': read_sockstat(),
            'tcp_states': read_tcp_states(),
        }
        netstat_history.append(pt)
        if len(netstat_history) > NETSTAT_POINTS:
            del netstat_history[0]
        time.sleep(NETSTAT_INTERVAL)

def get_cookie(headers, name):
    cookies = headers.get('Cookie', '')
    for part in cookies.split(';'):
        part = part.strip()
        if part.startswith(name + '='):
            return part.split('=', 1)[1]
    return None

def check_auth(headers):
    token = get_cookie(headers, 'token')
    if token and token in TOKENS:
        return True
    return False

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            if check_auth(self.headers):
                self.send_response(200)
                self.send_header('Content-Type','text/html;charset=utf-8')
                self.end_headers()
                with open(INDEX_HTML,'rb') as f: self.wfile.write(f.read())
            else:
                self.send_response(302)
                self.send_header('Location','/login')
                self.end_headers()
        elif self.path == '/login':
            self.send_response(200)
            self.send_header('Content-Type','text/html;charset=utf-8')
            self.end_headers()
            with open(LOGIN_HTML,'rb') as f: self.wfile.write(f.read())
        elif self.path == '/api/data':
            if check_auth(self.headers):
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Access-Control-Allow-Origin','*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'iface': iface_name,
                    'data': history
                }).encode())
            else:
                self.send_response(401)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error':'Unauthorized'}).encode())
        elif self.path == '/api/netstat':
            if check_auth(self.headers):
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Access-Control-Allow-Origin','*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'data': netstat_history,
                }).encode())
            else:
                self.send_response(401)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error':'Unauthorized'}).encode())
        elif self.path == '/logout':
            token = get_cookie(self.headers, 'token')
            if token and token in TOKENS:
                del TOKENS[token]
            self.send_response(302)
            self.send_header('Location','/login')
            self.send_header('Set-Cookie','token=; Path=/; Max-Age=0')
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/api/login':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                username = data.get('username', '')
                password = data.get('password', '')
                if username in CREDENTIALS and CREDENTIALS[username] == password:
                    token = secrets.token_hex(32)
                    TOKENS[token] = username
                    self.send_response(200)
                    self.send_header('Content-Type','application/json')
                    self.send_header('Set-Cookie', f'token={token}; Path=/; HttpOnly; SameSite=Strict')
                    self.end_headers()
                    self.wfile.write(json.dumps({'ok': True}).encode())
                else:
                    self.send_response(401)
                    self.send_header('Content-Type','application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error':'Invalid username or password'}).encode())
            except Exception:
                self.send_response(400)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error':'Bad request'}).encode())
        else:
            self.send_error(404)

    def log_message(self, fmt, *args): pass

class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

if __name__ == '__main__':
    load_config()
    iface_name = detect_iface()
    print(f'Interface: {iface_name}')
    cur = read_dev(iface_name)
    if cur: prev, prev_ts = cur, time.time()
    threading.Thread(target=collect, args=(iface_name,), daemon=True).start()
    threading.Thread(target=ping_loop, daemon=True).start()
    threading.Thread(target=collect_netstat, daemon=True).start()
    s = S(('0.0.0.0', PORT), H)
    print(f'http://0.0.0.0:{PORT}')
    s.serve_forever()
