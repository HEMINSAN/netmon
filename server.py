#!/usr/bin/env python3
import http.server, socketserver, json, time, threading, subprocess, os

PORT = 8080
MAX_POINTS = 120
PEERS = {'B': '10.0.10.14', 'C': '120.131.13.144'}

history = []
prev = None
prev_ts = 0
lat_b = -1
lat_c = -1

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

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type','text/html;charset=utf-8')
            self.end_headers()
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
            with open(p,'rb') as f: self.wfile.write(f.read())
        elif self.path == '/api/data':
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Access-Control-Allow-Origin','*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'iface': iface_name,
                'data': history
            }).encode())
        else:
            self.send_error(404)
    def log_message(self, fmt, *args): pass

class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

if __name__ == '__main__':
    iface_name = detect_iface()
    print(f'Interface: {iface_name}')
    cur = read_dev(iface_name)
    if cur: prev, prev_ts = cur, time.time()
    threading.Thread(target=collect, args=(iface_name,), daemon=True).start()
    threading.Thread(target=ping_loop, daemon=True).start()
    s = S(('0.0.0.0', PORT), H)
    print(f'http://0.0.0.0:{PORT}')
    s.serve_forever()
