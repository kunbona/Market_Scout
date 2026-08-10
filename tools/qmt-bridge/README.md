# QMT Bridge

把 Windows VM 上的 miniQMT / xtquant 能力通过 HTTP 暴露给 Mac 端的 Market Scout 主服务。

## 架构

```
┌──────────────────────────┐         HTTP/JSON          ┌────────────────────────────┐
│  Mac (主进程 server.py)   │  ◄────────────────────►    │  Windows VM                │
│                          │                            │                            │
│  fetcher/qmt_data_api.py │                            │  tools/qmt-bridge/         │
│  ┌──────────────────┐    │   POST /qmt/call           │  ┌──────────────────────┐  │
│  │ 检测 BRIDGE_URL  │───►│   Authorization: Bearer    │  │ Flask + xtquant     │  │
│  │ → HTTP 客户端    │    │                            │  │ 转发到本机 miniQMT  │  │
│  │ 否则 → 本地直连  │    │   GET  /health             │  └──────────────────────┘  │
│  └──────────────────┘    │   GET  /qmt/version        │              ↓              │
│                          │   GET  /qmt/methods        │        miniQMT 客户端        │
└──────────────────────────┘                            └────────────────────────────┘
```

## 部署（Windows VM 端，一次性）

### 1. 准备环境

```powershell
# Python 3.10+ (从 python.org 下载，记得勾 Add to PATH)
python --version

# 把本目录复制到 VM（任意位置，比如 D:\Tools\qmt-bridge\）
cd D:\Tools\qmt-bridge

# 安装依赖
pip install -r requirements.txt

# 安装 xtquant（券商通常提供，参考 QMT 客户端文档）
pip install xtquant

# 验证 xtquant 能 import
python -c "from xtquant import xtdata; print(xtdata.__version__)"
```

### 2. 启动 miniQMT 客户端并登录

先打开券商 QMT 客户端，完成登录和交易密码验证。**登录成功后再启动 bridge**。

### 3. 启动 bridge

编辑 `start_bridge.bat`：

```bat
set QMT_BRIDGE_TOKEN=<一串长随机字符串，比如 openssl rand -hex 32 的输出>
```

双击 `start_bridge.bat`。首次启动会弹防火墙窗询问是否允许 5001 端口，选**允许**。

看到以下输出说明成功：

```
启动 QMT Bridge：0.0.0.0:5001
白名单方法数：12
```

### 4. 验证（在本机浏览器）

打开 `http://127.0.0.1:5000/health`（**注意不是 5001**——是 QMT 客户端自己的 web 端口，不是 bridge）。**bridge 是 5001**。

在 PowerShell 验证：

```powershell
curl http://127.0.0.1:5001/health
curl -H "Authorization: Bearer <你的 token>" http://127.0.0.1:5001/qmt/methods
```

返回 JSON 正常说明 bridge 通了。

### 5. 获取 VM 的 IP

```powershell
ipconfig
```

找到 IPv4 地址（比如 `192.168.1.100`）。Mac 端要用这个地址。

### 6. Mac 端配置

在 Mac 项目的 `.env.local` 里加：

```
QMT_BRIDGE_URL=http://192.168.1.100:5001
QMT_BRIDGE_TOKEN=<跟 VM 端完全一致>
QMT_ENABLED=true
QMT_PATH=
```

注意：`QMT_PATH` 在 Mac 端**留空即可**，xtquant 不在 Mac 上跑。

## 开机自启（推荐）

### 方案 A：启动文件夹（最简单）

1. 按 `Win + R`，输入 `shell:startup`，回车
2. 把 `start_bridge.bat` 的**快捷方式**拖进打开的文件夹
3. 重启验证

### 方案 B：任务计划程序（更稳）

1. 搜索"任务计划"打开
2. 右侧"创建任务"
3. 常规 → 名称 "QMT Bridge"，勾选"不管用户是否登录都要运行"
4. 触发器 → 新建 → 登录时
5. 操作 → 新建 → 启动程序 → 选 `start_bridge.bat` 路径
6. 条件 → 取消"只有在计算机使用交流电源时才启动"
7. 确定

## API 端点

| 端点 | 用途 | 鉴权 |
|---|---|---|
| `GET  /health` | 健康检查（含 xtquant 是否就绪） | ❌ 不需要 |
| `GET  /qmt/version` | xtquant 版本 | ✅ Bearer |
| `GET  /qmt/methods` | 列出白名单方法 | ✅ Bearer |
| `POST /qmt/connect` | 测试 miniQMT 连接 | ✅ Bearer |
| `POST /qmt/call` | **通用代理：调任何白名单方法** | ✅ Bearer |

### `/qmt/call` 协议

请求：

```json
POST /qmt/call
Authorization: Bearer <token>
Content-Type: application/json

{
  "method": "get_full_tick",
  "args": [["600519.SH", "000858.SZ"]],
  "kwargs": {}
}
```

成功响应：

```json
{
  "ok": true,
  "data": {
    "result": { "600519.SH": { "lastPrice": 1680.5, ... }, ... },
    "elapsed_ms": 142,
    "method": "get_full_tick"
  }
}
```

失败响应：

```json
{
  "ok": false,
  "error": "方法「foo」不在白名单内。可调方法见 GET /qmt/methods"
}
```

错误码：
- 400 参数错误（method 缺失、args/kwargs 类型不对、xtdata 报 TypeError）
- 401 未授权（缺 token / token 错）
- 403 方法不在白名单
- 404 xtdata 上无此方法
- 500 xtdata 内部异常
- 503 xtquant 未安装

## 添加新的 xtquant 方法

只动一个文件 `allowlist.py`：

```python
ALLOWED_METHODS.add("get_financial_data")
```

重启 bridge 生效。Mac 端调用无需改动——`qmt_data_api.py` 通过 `qmt_client._call("get_financial_data", ...)` 直接转发。

## 排错

| 现象 | 可能原因 | 排查 |
|---|---|---|
| Mac 端报 "QMT 不可用" | bridge URL 配错 / 端口被防火墙挡 | 在 Mac 终端 `curl http://VM_IP:5001/health` |
| `xtdata_available: false` | xtquant 没装 / Python 环境不对 | `python -c "from xtquant import xtdata"` 看具体报错 |
| `connect failed: 行情服务器连接失败` | miniQMT 客户端没登录 / 端口冲突 | 先手动打开 QMT 客户端登录成功后再启 bridge |
| `/qmt/call` 一直超时 | VM IP 变了 / 路由器隔离 | `ping VM_IP`；检查 VM 防火墙入站规则 |
| `get_full_tick` 返回空但 health 正常 | xtquant 客户端未订阅全市场行情 | QMT 客户端主界面发起一次"批量订阅"或查询任意股票 |
| token 设置后 bridge 仍报 401 | 环境变量没生效 | 在 bridge 窗口看启动日志确认 token 前 8 位 |

## 安全

- **必须设 QMT_BRIDGE_TOKEN**：未设置时 bridge 拒绝所有请求，避免误开端口
- **监听地址**：默认 `0.0.0.0`（同网段可访问）。如果 VM 和 Mac 直连可改 `127.0.0.1`（但 Mac 必须 VM 内才能访问——一般用不到）
- **HTTPS**：桥本身只走 HTTP，**仅在可信 LAN 内使用**。如需跨公网建议前置 Nginx + TLS，或者直接上 WireGuard/Tailscale
- **白名单**：Mac 端只能调白名单内的方法，xtquant 内部所有危险操作（订阅持久化、文件写入等）都不可达

## 文件清单

```
tools/qmt-bridge/
├── server.py           # Flask 桥主程序
├── allowlist.py        # 方法白名单常量
├── requirements.txt    # Python 依赖
├── start_bridge.bat    # Windows 启动脚本（含开机自启说明）
└── README.md           # 本文件
```
