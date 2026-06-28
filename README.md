# 🐙 ヤチヨのキャッシュプール～☆

> Yachiyo's Local Semantic Cache Pool

基于本地 Ollama 的语义缓存池。对 API 请求做精确匹配 + 语义相似度搜索，命中时**0 tokens 返回**，不命中时透明转发上游 API 并自动缓存。

## 架构

```
请求 → cache_proxy (localhost:18791)
           ↓
    语义缓存 (embeddinggemma + SQLite)
           ↓
    命中？ → 是 → 0 tokens 返回 ✅
    命中？ → 否 → 转发 DeepSeek API → 自动缓存 → 返回
```

- 🧠 **Embedding**: `embeddinggemma:300m-qat-q4_0` (Ollama 本地，0.3s 响应)
- 💾 **存储**: SQLite (WAL 模式)
- 🎯 **精确匹配**: SHA-256 hash，秒速命中
- 🔍 **语义匹配**: 余弦相似度，阈值可调（默认 0.80）
- ⏱ **TTL**: 7 天自动过期

## 快速上手

```bash
# 1. 确保 Ollama 有 embeddinggemma
ollama pull embeddinggemma:300m-qat-q4_0

# 2. 启动代理
python3 cache_proxy.py
# 或
bash start-cache.sh

# 3. 配置上游指向 localhost:18791
# 在你的应用中设置 base_url = "http://localhost:18791"

# 4. 手动操作缓存
bash cache.sh stats           # 查看统计
bash cache.sh search "xxx"    # 搜索缓存
bash cache.sh demo            # 跑 Demo
bash cache.sh put "Q" "A"     # 手动存入
bash cache.sh clean           # 清理过期
```

## 文件说明

| 文件 | 说明 |
|:----|:-----|
| `cache_pool.py` | 核心库：embedding + 数据库 + 搜索 |
| `cache_proxy.py` | HTTP 代理服务器（Flask） |
| `cache.sh` | 手动操作 CLI |

## 配置

在 `cache_pool.py` 中可调参数：

```python
SIMILARITY_THRESHOLD = 0.80  # 语义匹配阈值
DEFAULT_TTL = 86400 * 7      # 缓存有效期（秒）
```

## Protocol

与 OpenAI-compatible API 兼容：

```bash
POST /chat/completions
Authorization: Bearer <your-api-key>
Content-Type: application/json

{
  "model": "deepseek-v4-flash",
  "messages": [{"role": "user", "content": "你好"}]
}
```

## License

MIT
