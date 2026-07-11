# 🐙 ヤチヨのキャッシュプール～☆

> Yachiyo's Local Semantic Cache Proxy

开包即用的语义缓存代理层。克隆下来一行命令就能跑。

对 API 请求做精确匹配 + 语义相似度搜索，命中时 **0 tokens 返回**，不命中时透明转发上游 API 并自动缓存。

## 快速开始

```bash
git clone https://github.com/Yachiyo1680/yachiyo-cache-pool
cd yachiyo-cache-pool
bash start-cache.sh
```

启动脚本会自动：
1. ✅ 安装 Python 依赖（Flask + requests）
2. ✅ 下载 llama.cpp server 二进制
3. ✅ 下载 embeddinggemma Q8_0 模型（~314MB）
4. ✅ 启动 embedding server（端口 8080）
5. ✅ 启动缓存代理（端口 18791）

全部一行搞定，无需手动配置。

## 架构

```
请求 → cache_proxy (localhost:18791)
           ↓
    语义缓存 (embeddinggemma + llama.cpp + SQLite)
           ↓  ┌─────────────────────────────┐
    命中？ → 是 → 0 tokens 返回 ✅
           ↓ 否
    转发 LLM API → 自动缓存 → 返回
```

## 手动操作缓存

```bash
bash cache.sh stats           # 查看统计
bash cache.sh search "xxx"    # 搜索缓存
bash cache.sh demo            # 跑 Demo
bash cache.sh put "Q" "A"     # 手动存入
bash cache.sh clean           # 清理过期
```

## 文件说明

| 文件 | 说明 |
|:----|:-----|
| `start-cache.sh` | 一键启动脚本（自动下载所有依赖） |
| `cache_pool.py` | 核心库：embedding + SQLite + 搜索/存储 |
| `cache_proxy.py` | HTTP 代理服务器（Flask，OpenAI 兼容） |
| `cache.sh` | 手动操作 CLI |
| `requirements.txt` | Python 依赖 |
| `models/` | 自动下载的 GGUF 模型放这里 |
| `bin/` | 自动下载的 llama-server 二进制放这里 |
| `cache_hit.log` | 命中/未命中日志（自动生成，>5MB 轮转） |

## 配置

在 `cache_pool.py` 中可调参数：

```python
SIMILARITY_THRESHOLD = 0.92     # 语义匹配阈值
EMBED_MODEL = "embeddinggemma"   # embedding 模型名（llama-server 用）
DEFAULT_TTL = 86400 * 7         # 缓存有效期（秒）
```

## 命中日志示例

```
[2026-07-11 15:09:17] HIT  | sim=1.0000 | saved=~180 tokens | query=今天天气怎么样
[2026-07-11 15:09:41] MISS | sim=N/A    | saved=~0 tokens   | query=UV-K5的Flash怎么烧
```

## Protocol

与 OpenAI-compatible API 完全兼容：

```bash
POST /chat/completions
Authorization: Bearer <your-api-key>
Content-Type: application/json

{
  "model": "deepseek-v4-flash",
  "messages": [{"role": "user", "content": "你好"}]
}
```

响应头 `X-Cache: exact` / `X-Cache: semantic` / `X-Cache: miss` 标识命中来源。

## 系统要求

- Linux x86_64 或 arm64/aarch64
- Python 3.8+
- curl
- 约 500MB 磁盘空间（含模型文件）
- 约 500MB 内存

## License

MIT
