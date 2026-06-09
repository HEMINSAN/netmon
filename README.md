# ⚡ NetMon

轻量级 Linux 服务器网络实时监控面板，两个文件、零依赖、秒级部署。

## 功能

- 📊 实时流量监控（BPS / PPS，入/出双方向）
- 🌐 多节点延迟监测（ping 延迟趋势）
- ⚠️ 丢包/错误统计
- 🖥️ 深色主题 ECharts 图表，2×2 宫格布局
- 📱 响应式设计，移动端自适应

## 快速开始

```bash
# 克隆
git clone https://github.com/<your-username>/netmon.git
cd netmon

# 启动（Python 3.6+，无需安装依赖）
python3 server.py
# → 访问 http://<服务器IP>:8080
```

## 架构

```
netmon/
├── index.html    # 前端单页面（ECharts 图表 + 轮询）
└── server.py     # 后端采集 + HTTP 服务（Python 标准库）
```

- **前端**：纯 HTML/CSS/JS + ECharts CDN，每秒轮询 `/api/data`
- **后端**：Python 标准库 `http.server`，三线程架构：
  - `collect` 线程：每秒读 `/proc/net/dev` 计算流量
  - `ping_loop` 线程：每 3 秒 ping 监控节点延迟
  - `HTTP` 线程：提供 `/` 页面和 `/api/data` 接口

## 配置

编辑 `server.py` 顶部的常量：

```python
PORT = 8080            # 监听端口
MAX_POINTS = 120       # 历史数据点数（默认 120 秒）
PEERS = {              # 监控对端节点
    'B': '10.0.10.14',
    'C': '120.131.13.144',
}
```

## License

MIT
