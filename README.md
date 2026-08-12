# ⚡ NetMon

轻量级 Linux 服务器网络/内存实时监控面板，两个文件、零依赖、秒级部署。

## 功能

- 📊 实时流量监控（BPS / PPS，入/出双方向）
- 🌐 多节点延迟监测（ping 延迟趋势）
- ⚠️ 丢包/错误统计
- 🖥️ **NUMA / 内存监控**（辅助分析异常程序导致的内存增长）
  - 逐节点 MemUsed 趋势（多 NUMA 逐节点一条线）
  - 内存分类拆解（匿名 anon / 文件 file / slab / shmem 堆叠）—— 判断是哪种内存在涨
  - Top-N 进程按 RSS 排序 + 各进程 NUMA 页分布 —— 定位是哪个进程在吃内存
  - Top 进程 RSS 趋势 —— 判断是否单调上涨（泄漏签名）
  - 长期 jsonl 日志（`numa_log.jsonl`），便于事后 grep / 画图
- 🔌 **网络关联指标监控**（辅助分析连接/fd 泄漏、队列溢出等网络问题）
  - TCP 连接状态分布（ESTABLISHED / TIME_WAIT / CLOSE_WAIT / SYN_RECV 等，堆叠）—— 看哪种状态堆积
  - 连接数趋势（ESTABLISHED / TIME_WAIT / CLOSE_WAIT / orphan）—— TIME_WAIT/CLOSE_WAIT 堆积信号
  - 文件描述符用量（已用 / 上限）—— fd 泄漏
  - TCP 质量（重传/s、SYN 重传/s、accept 队列溢出/s）—— 连接质量
  - Socket 统计（TCP / UDP / UNIX / 总数）—— socket 占用
  - Top-N 进程按 fd 数排序 —— 定位 fd 泄漏进程
  - conntrack 用量（若内核启用连接跟踪则显示）
- 🖥️ 深色主题 ECharts 图表，响应式布局
- 📱 移动端自适应

## 快速开始

```bash
# 克隆
git clone https://github.com/<your-username>/netmon.git
cd netmon

# 配置登录账号
cp config.example.json config.json
# 编辑 config.json 改 username/password

# 启动（Python 3.6+，无需安装依赖）
python3 server.py
# → 访问 http://<服务器IP>:8080
```

## 架构

```
netmon/
├── index.html            # 前端单页面（ECharts 图表 + 轮询）
├── server.py             # 后端采集 + HTTP 服务（Python 标准库）
├── config.example.json   # 配置示例
└── config.json           # 实际配置（gitignored，含密码）
```

- **前端**：纯 HTML/CSS/JS + ECharts CDN；网络每 1 秒轮询 `/api/data`，内存每 5 秒轮询 `/api/numa`，网络关联指标每 5 秒轮询 `/api/netstat`
- **后端**：Python 标准库 `http.server`，五线程架构：
  - `collect` 线程：每秒读 `/proc/net/dev` 计算流量
  - `ping_loop` 线程：每 3 秒 ping 监控节点延迟
  - `collect_mem` 线程：每 5 秒读 `/sys/devices/system/node/nodeN`（meminfo + numastat）+ `/proc/meminfo` + top-N 进程的 `/proc/<pid>/numa_maps`
  - `collect_netstat` 线程：每 5 秒读 `/proc/sys/fs/file-nr`、`/proc/net/sockstat`、`/proc/net/tcp[*]`、`/proc/net/snmp`、`/proc/net/netstat`、`/proc/softirqs`、conntrack + top fd 进程
  - `HTTP` 线程：提供 `/`、`/login`、`/api/data`、`/api/numa`、`/api/netstat` 接口

## 配置

`config.json`（除 `username`/`password` 外均可选，缺省用默认值）：

```json
{
  "username": "admin",
  "password": "changeme",
  "mem_interval": 5,
  "mem_points": 2880,
  "top_n": 10,
  "mem_log_file": "numa_log.jsonl",
  "netstat_interval": 5,
  "netstat_points": 2880,
  "top_fds_n": 10
}
```

| 键 | 默认 | 说明 |
|---|---|---|
| `username`/`password` | admin/changeme | 登录账号 |
| `mem_interval` | 5 | 内存采样间隔（秒） |
| `mem_points` | 2880 | 内存历史保留点数（5s × 2880 = 4h） |
| `top_n` | 10 | 展示的 top 进程数量 |
| `mem_log_file` | `numa_log.jsonl` | 长期内存日志文件名，置空字符串则关闭 |
| `netstat_interval` | 5 | 网络关联指标采样间隔（秒） |
| `netstat_points` | 2880 | 网络关联指标历史保留点数（5s × 2880 = 4h） |
| `top_fds_n` | 10 | 展示的 top fd 进程数量 |

`server.py` 顶部常量仍可硬编码（PORT、MAX_POINTS、PEERS）。

## API

| 路径 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/api/data` | GET | Cookie token | 网络流量/延迟历史 |
| `/api/numa` | GET | Cookie token | 内存/NUMA 历史 + 当前 top 进程 |
| `/api/netstat` | GET | Cookie token | 网络关联指标历史（连接/文件/socket/TCP 质量）+ 当前 top fd 进程 |
| `/api/login` | POST | - | 登录获取 token |
| `/logout` | GET | - | 注销 |

## License

MIT
