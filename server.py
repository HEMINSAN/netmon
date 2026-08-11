#!/usr/bin/env python3
import http.server, socketserver, json, time, threading, subprocess, os, hashlib, secrets, urllib.parse, glob

PORT = 8080
MAX_POINTS = 120
PEERS = {'B': '10.0.10.14', 'C': '120.131.13.144'}

# NUMA / 内存监控默认参数（可被 config.json 覆盖）
MEM_INTERVAL = 5        # 采样间隔（秒）
MEM_POINTS = 2880       # 内存历史保留点数（5s × 2880 = 4h）
TOP_N = 10              # top 进程数量
MEM_LOG_FILE = 'numa_log.jsonl'   # 长期日志文件，置空字符串则关闭

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
LOGIN_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'login.html')
INDEX_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')

TOKENS = {}
CREDENTIALS = {}

def load_config():
    global CREDENTIALS, MEM_INTERVAL, MEM_POINTS, TOP_N, MEM_LOG_FILE
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
            CREDENTIALS = {cfg['username']: cfg['password']}
            MEM_INTERVAL = cfg.get('mem_interval', MEM_INTERVAL)
            MEM_POINTS = cfg.get('mem_points', MEM_POINTS)
            TOP_N = cfg.get('top_n', TOP_N)
            MEM_LOG_FILE = cfg.get('mem_log_file', MEM_LOG_FILE)
    except Exception as e:
        print(f'Warning: failed to load config.json: {e}')
        CREDENTIALS = {'admin': 'admin'}

history = []
prev = None
prev_ts = 0
lat_b = -1
lat_c = -1

# 内存历史与状态
mem_history = []
mem_top = []
mem_avail = 0
mem_prev_numastat = {}
mem_prev_ts = 0

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

# ---- NUMA / 内存采集 ----
def read_node_meminfo(node_dir):
    d = {}
    try:
        with open(os.path.join(node_dir, 'meminfo')) as f:
            for line in f:
                if ':' not in line:
                    continue
                k, v = line.split(':', 1)
                parts = k.split()
                if len(parts) >= 3 and parts[0] == 'Node':
                    key = ' '.join(parts[2:])      # "Node 0 MemTotal" -> "MemTotal"
                else:
                    key = k.strip()
                d[key] = int(v.split()[0])
    except Exception:
        pass
    return d

def read_node_numastat(node_dir):
    d = {}
    try:
        with open(os.path.join(node_dir, 'numastat')) as f:
            for line in f:
                p = line.split()
                if len(p) == 2:
                    try:
                        d[p[0]] = int(p[1])
                    except Exception:
                        pass
    except Exception:
        pass
    return d

def read_mem_avail():
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable'):
                    return int(line.split()[1])
    except Exception:
        pass
    return -1

def read_top_procs(n):
    procs = []
    try:
        for name in os.listdir('/proc'):
            if not name.isdigit():
                continue
            try:
                rss = 0
                pname = ''
                with open(f'/proc/{name}/status') as f:
                    for line in f:
                        if line.startswith('Name:'):
                            pname = line.split(':', 1)[1].strip()
                        elif line.startswith('VmRSS:'):
                            rss = int(line.split()[1])
                if rss > 0:
                    procs.append((int(name), pname, rss))
            except Exception:
                pass
    except Exception:
        pass
    procs.sort(key=lambda x: -x[2])
    out = []
    for pid, pname, rss in procs[:n]:
        nodes = {}
        try:
            with open(f'/proc/{pid}/numa_maps') as f:
                for line in f:
                    for tok in line.split():
                        if tok.startswith('N') and '=' in tok:
                            nk, nv = tok.split('=', 1)
                            try:
                                nodes[nk] = nodes.get(nk, 0) + int(nv)
                            except Exception:
                                pass
        except Exception:
            pass
        out.append({
            'pid': pid,
            'name': pname,
            'rss_kb': rss,
            'nodes': {k: v * 4 for k, v in nodes.items()},   # pages -> kB
        })
    return out

def collect_mem():
    global mem_history, mem_top, mem_avail, mem_prev_numastat, mem_prev_ts
    node_dirs = [os.path.dirname(p) for p in sorted(glob.glob('/sys/devices/system/node/node[0-9]*/meminfo'))]
    log_path = None
    if MEM_LOG_FILE:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), MEM_LOG_FILE)
    while True:
        now = time.time()
        dt = now - mem_prev_ts if mem_prev_ts else 0
        nodes_out = []
        numastat_out = []
        for nd in node_dirs:
            mi = read_node_meminfo(nd)
            ns = read_node_numastat(nd)
            node_id = int(os.path.basename(nd).replace('node', ''))
            nodes_out.append({
                'node': node_id,
                'total_kb': mi.get('MemTotal', 0),
                'free_kb': mi.get('MemFree', 0),
                'used_kb': mi.get('MemUsed', 0),
                'anon_kb': mi.get('Active(anon)', 0) + mi.get('Inactive(anon)', 0) + mi.get('AnonPages', 0),
                'file_kb': mi.get('Active(file)', 0) + mi.get('Inactive(file)', 0),
                'slab_kb': mi.get('Slab', 0),
                'shmem_kb': mi.get('Shmem', 0),
                'hugepages': mi.get('HugePages_Total', 0),
            })
            prev_ns = mem_prev_numastat.get(node_id)
            if prev_ns and dt > 0:
                numastat_out.append({
                    'node': node_id,
                    'hit_rate': round((ns.get('numa_hit', 0) - prev_ns.get('numa_hit', 0)) / dt, 0),
                    'foreign_rate': round((ns.get('numa_foreign', 0) - prev_ns.get('numa_foreign', 0)) / dt, 0),
                    'other_rate': round((ns.get('other_node', 0) - prev_ns.get('other_node', 0)) / dt, 0),
                    'miss_rate': round((ns.get('numa_miss', 0) - prev_ns.get('numa_miss', 0)) / dt, 0),
                })
            else:
                numastat_out.append({'node': node_id, 'hit_rate': 0, 'foreign_rate': 0, 'other_rate': 0, 'miss_rate': 0})
            mem_prev_numastat[node_id] = ns
        mem_avail = read_mem_avail()
        procs = read_top_procs(TOP_N)
        pt = {
            't': round(now, 1),
            'nodes': nodes_out,
            'mem_avail_kb': mem_avail,
            'numastat': numastat_out,
            'procs': procs,
        }
        mem_history.append(pt)
        if len(mem_history) > MEM_POINTS:
            del mem_history[0]
        mem_top = procs
        mem_prev_ts = now
        if log_path:
            try:
                with open(log_path, 'a') as f:
                    f.write(json.dumps(pt) + '\n')
            except Exception:
                pass
        time.sleep(MEM_INTERVAL)

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
        elif self.path == '/api/numa':
            if check_auth(self.headers):
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Access-Control-Allow-Origin','*')
                self.end_headers()
                nodes_list = sorted({nd['node'] for nd in (mem_history[-1]['nodes'] if mem_history else [])})
                self.wfile.write(json.dumps({
                    'nodes': nodes_list,
                    'data': mem_history,
                    'top': mem_top,
                    'mem_avail_kb': mem_avail,
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
    threading.Thread(target=collect_mem, daemon=True).start()
    s = S(('0.0.0.0', PORT), H)
    print(f'http://0.0.0.0:{PORT}')
    s.serve_forever()
