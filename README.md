# 🐙 ヤチヨのキャッシュプール～☆

> Yachiyo's Local Semantic Cache Proxy

基于 **llama.cpp server** 的语义缓存代理层。对 API 请求做精确匹配 + 语义相似度搜索，命中时 **0 tokens 返回**，不命中时透明转发上游 API 并自动缓存。

## 架构

```
请求 → cache_proxy (localhost:18791)
           ↓
    语义缓存 (embeddinggemma + SQLite)
           ↓  ┌─────────────────────────────┐
    命中？ → 是 → 0 tokens 返回 ✅
           ↓ 否
    转发 DeepSeek API → 自动缓存 → 返回
```

- 🧠 **Embedding**: `embeddinggemma-300m-qat-Q8_0` (llama.cpp server, 768维, 本地推理)
- 💾 **存储**: SQLite (WAL 模式, 向量和内联存储)
- 🎯 **精确匹配**: SHA-256 hash，零开销命中
- 🔍 **语义匹配**: 余弦相似度，阈值 0.92（可调）
- 📊 **命中日志**: `cache_hit.log` — 记录每次命中/未命中及预估节省 Token
- ⏱ **TTL**: 7 天自动过期

## 快速上手

### 前置：启动 llama.cpp embedding server

```bash
# GGUF 文件路径
GGUF="/home/pi/.node-llama-cpp/models/hf_ggml-org_embeddinggemma-300m-qat-Q8_0.gguf"

# 启动服务（CPU 推理，仅 embedding 模式）
nohup /usr/local/lib/ollama/llama-server \
  --model "$GGUF" --port 8080 --host 127.0.0.1 \
  --embedding --ctx-size 512 --batch-size 512 \
  --threads 4 --no-webui --no-mmap \
  --pooling mean --embd-normalize 2 \
  > /dev/null 2>&1 &

# 验证
curl -X POST http://localhost:8080/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"input":"你好","model":"embeddinggemma"}'
```

### 启动缓存代理

```bash
python3 cache_proxy.py
# 或
bash start-cache.sh
```

### 配置上游指向 localhost:18791

在你的应用中将 `base_url` 改为：

```
http://localhost:18791
```

API Key 自动透传，无需额外配置。

### 手动操作缓存

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
| `cache_pool.py` | 核心库：embedding + 数据库 + 搜索/存储 |
| `cache_proxy.py` | HTTP 代理服务器（Flask），带命中日志 |
| `cache_hit.log` | 命中/未命中日志（自动生成，>5MB 轮转） |
| `cache.sh` | 手动操作 CLI |

## 配置

在 `cache_pool.py` 中可调参数：

```python
SIMILARITY_THRESHOLD = 0.92     # 语义匹配阈值（技术问答建议 0.92+）
USE_LLAMA_SERVER_DIRECT = True  # True=llama.cpp server, False=Ollama
LLAMA_SERVER_URL = "http://localhost:8080/v1/embeddings"
OLLAMA_URL = "http://localhost:11434/api/embeddings"  # Fallback
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

## License

MIT
