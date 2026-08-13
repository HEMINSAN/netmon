# ⚡ NetMon

轻量级 Linux 服务器网络实时监控面板，两个文件、零依赖、秒级部署。

## 功能

- 📊 实时流量监控（BPS / PPS，入/出双方向）
- 🌐 多节点延迟监测（ping 延迟趋势）
- ⚠️ 丢包/错误统计
- 🔌 Socket / TCP 状态监控
  - TCP 连接状态分布（ESTABLISHED / LISTEN / TIME_WAIT / CLOSE_WAIT / SYN_RECV 等，堆叠）
  - Socket 统计趋势（TCP inuse / UDP / UNIX / 总数）
- 🖥️ 深色主题 ECharts 图表，2×2 宫格布局
- 📱 响应式设计，移动端自适应

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

- **前端**：纯 HTML/CSS/JS + ECharts CDN；首屏 `GET /api/config` 拿 `chart_interval`，网络图与 socket/TCP 图均按 `chart_interval`（默认 3s，范围 1~30s）轮询 `/api/data`、`/api/netstat`
- **后端**：Python 标准库 `http.server`，三线程架构：
  - `collect` 线程：每秒读 `/proc/net/dev` 计算流量（采集≠绘图，历史点节奏不变，仅前端拉取节奏跟随 `chart_interval`）
  - `ping_loop` 线程：每 3 秒 ping 监控节点延迟
  - `collect_netstat` 线程：按 `chart_interval` 采样 `/proc/net/sockstat`+`sockstat6`、`/proc/net/tcp`+`tcp6`（连接状态分布）、`/proc/net/unix`
  - `HTTP` 线程：提供 `/`、`/login`、`/api/config`、`/api/data`、`/api/netstat` 接口

## 配置

`config.json`（除 `username`/`password` 外均可选，缺省用默认值）：

```json
{
  "username": "admin",
  "password": "changeme",
  "chart_interval": 3,
  "netstat_points": 2880
}
```

| 键 | 默认 | 说明 |
|---|---|---|
| `username`/`password` | admin/changeme | 登录账号 |
| `chart_interval` | 3 | 绘图间隔（秒），范围 1~30，越界自动钳到 [1,30]；前端绘图与后端 socket/TCP 采样共用此间隔 |
| `netstat_points` | 2880 | 历史保留点数（默认 `chart_interval` 3s × 2880 ≈ 2.4h） |

`server.py` 顶部常量仍可硬编码（PORT、MAX_POINTS、PEERS）。

## API

| 路径 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/api/data` | GET | Cookie token | 网络流量/延迟历史 |
| `/api/netstat` | GET | Cookie token | socket 统计 + TCP 连接状态历史 |
| `/api/config` | GET | - | 返回前端配置（`chart_interval` 等，首屏拉一次） |
| `/api/login` | POST | - | 登录获取 token |
| `/logout` | GET | - | 注销 |

## License

MIT
