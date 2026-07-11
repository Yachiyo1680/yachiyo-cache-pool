#!/usr/bin/env python3
"""
🦞 Yachiyo's Local Cache Pool
ローカルキャッシュプール～☆

A semantic cache for API responses. Reduces duplicate API calls and saves tokens!
"""

import json
import hashlib
import sqlite3
import time
import sys
import os
import math
import urllib.request
import urllib.error

# === Configuration (从 config.yaml 加载) ===
def load_config():
    """Load config from config.yaml or return defaults."""
    # Look for config.yaml next to this file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.yaml")
    
    defaults = {
        "embedding": {
            "backend": "llama-server",
            "llama_url": "http://localhost:8080/v1/embeddings",
            "ollama_url": "http://localhost:11434/api/embeddings",
            "ollama_model": "embeddinggemma:300m-qat-q4_0"
        },
        "cache": {
            "similarity_threshold": 0.92,
            "ttl_seconds": 604800
        },
        "proxy": {
            "host": "0.0.0.0",
            "port": 18791,
            "cache_non_streaming": True,
            "cache_streaming": False
        },
        "upstream": {
            "base_url": "https://api.deepseek.com",
            "timeout_seconds": 120
        }
    }
    
    if not os.path.exists(config_path):
        return defaults
    
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        # Merge: user config overrides defaults
        merged = defaults
        for section, values in user_config.items():
            if section in merged and isinstance(values, dict):
                merged[section].update(values)
            else:
                merged[section] = values
        return merged
    except ImportError:
        print("⚠️ pyyaml 未安装，使用默认配置。pip install pyyaml")
        return defaults
    except Exception as e:
        print(f"⚠️ 配置文件读取失败: {e}，使用默认配置")
        return defaults

CFG = load_config()

# === 将配置展开为模块变量 ===
_embed = CFG["embedding"]
_cache = CFG["cache"]
_proxy = CFG["proxy"]
_upstream = CFG["upstream"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "cache.db")
OLLAMA_URL = _embed["ollama_url"]
LLAMA_SERVER_URL = _embed["llama_url"]
USE_LLAMA_SERVER_DIRECT = _embed["backend"] == "llama-server"
EMBED_MODEL = _embed.get("ollama_model", "bge-m3")
SIMILARITY_THRESHOLD = _cache["similarity_threshold"]
DEFAULT_TTL = _cache["ttl_seconds"]
MAX_RESULTS = 5

# === SQLite Setup ===
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent access
    conn.execute("PRAGMA synchronous=NORMAL")
    _init_schema(conn)
    return conn

def _init_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cache_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exact_hash TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            query TEXT NOT NULL,
            response TEXT NOT NULL,
            embedding BLOB,
            created_at REAL NOT NULL,
            last_hit_at REAL,
            hit_count INTEGER DEFAULT 0,
            ttl_seconds INTEGER DEFAULT 86400,
            metadata TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_exact_hash ON cache_entries(exact_hash);
        CREATE INDEX IF NOT EXISTS idx_created ON cache_entries(created_at);
    """)

# === Embedding ===
def get_embedding(text: str) -> list:
    """Get embedding vector from local llama-server or Ollama."""
    if USE_LLAMA_SERVER_DIRECT:
        # llama.cpp server: /v1/embeddings (OpenAI-compatible)
        data = json.dumps({
            "input": text,
            "model": "embeddinggemma"
        }).encode("utf-8")
        req = urllib.request.Request(
            LLAMA_SERVER_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                return result["data"][0]["embedding"]
        except Exception as e:
            raise RuntimeError(f"llama-server embedding failed: {e}")
    else:
        # Ollama API
        data = json.dumps({
            "model": EMBED_MODEL,
            "prompt": text
        }).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                return result["embedding"]
        except Exception as e:
            raise RuntimeError(f"Ollama embedding failed: {e}")

def cosine_similarity(a: list, b: list) -> float:
    """Calculate cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    
    dot = sum(av * bv for av, bv in zip(a, b))
    norm_a = math.sqrt(sum(av * av for av in a))
    norm_b = math.sqrt(sum(bv * bv for bv in b))
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return dot / (norm_a * norm_b)

def serialize_embedding(emb: list) -> bytes:
    """Serialize float list to bytes for storage."""
    return json.dumps(emb).encode("utf-8")

def deserialize_embedding(data: bytes) -> list:
    """Deserialize bytes back to float list."""
    return json.loads(data.decode("utf-8"))

# === Core Cache Operations ===

def exact_hash(query: str, model: str = "") -> str:
    """Generate SHA-256 hash for exact matching."""
    return hashlib.sha256(f"{model}||{query}".encode("utf-8")).hexdigest()

def _is_expired(entry: sqlite3.Row) -> bool:
    """Check if a cache entry has expired."""
    age = time.time() - entry["created_at"]
    return age > entry["ttl_seconds"]

def search(query: str, model: str = "", threshold: float = SIMILARITY_THRESHOLD) -> dict | None:
    """
    Search cache for a matching response.
    First tries exact match, then semantic search.
    Returns cached response dict or None.
    """
    db = get_db()
    try:
        h = exact_hash(query, model)
        now = time.time()
        
        # Step 1: Exact match (fastest)
        row = db.execute(
            "SELECT * FROM cache_entries WHERE exact_hash = ?",
            (h,)
        ).fetchone()
        
        if row and not _is_expired(row):
            # Update hit stats
            db.execute(
                "UPDATE cache_entries SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
                (now, row["id"])
            )
            db.commit()
            return {
                "hit": True,
                "match_type": "exact",
                "response": row["response"],
                "metadata": json.loads(row["metadata"]),
                "hit_count": row["hit_count"] + 1,
                "entry_id": row["id"]
            }
        
        # Step 2: Semantic search (slower, but catches similar queries)
        query_emb = get_embedding(query)
        
        # Get all non-expired entries with embeddings
        rows = db.execute(
            "SELECT * FROM cache_entries WHERE embedding IS NOT NULL AND created_at > ?",
            (now - DEFAULT_TTL,)
        ).fetchall()
        
        best_match = None
        best_score = 0.0
        
        for row in rows:
            try:
                stored_emb = deserialize_embedding(row["embedding"])
                score = cosine_similarity(query_emb, stored_emb)
                
                if score > best_score and score >= threshold:
                    best_score = score
                    best_match = row
            except Exception:
                continue
        
        if best_match:
            db.execute(
                "UPDATE cache_entries SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
                (now, best_match["id"])
            )
            db.commit()
            return {
                "hit": True,
                "match_type": "semantic",
                "similarity": round(best_score, 4),
                "response": best_match["response"],
                "metadata": json.loads(best_match["metadata"]),
                "hit_count": best_match["hit_count"] + 1,
                "entry_id": best_match["id"]
            }
        
        return {"hit": False}
    
    finally:
        db.close()

def store(query: str, response: str, model: str = "",
          embedding: list = None, metadata: dict = None,
          ttl: int = DEFAULT_TTL) -> int:
    """
    Store a query-response pair in the cache.
    Returns the entry ID.
    """
    db = get_db()
    try:
        h = exact_hash(query, model)
        now = time.time()
        
        emb_bytes = serialize_embedding(embedding) if embedding else None
        
        # Check if exact hash already exists
        existing = db.execute(
            "SELECT id, hit_count FROM cache_entries WHERE exact_hash = ?",
            (h,)
        ).fetchone()
        
        if existing:
            # Update existing entry
            db.execute(
                """UPDATE cache_entries SET
                    response = ?, embedding = COALESCE(?, embedding),
                    created_at = ?, last_hit_at = ?, metadata = ?,
                    ttl_seconds = ?
                WHERE id = ?""",
                (response, emb_bytes, now, now,
                 json.dumps(metadata or {}), ttl, existing["id"])
            )
            entry_id = existing["id"]
        else:
            cursor = db.execute(
                """INSERT INTO cache_entries
                    (exact_hash, model, query, response, embedding,
                     created_at, last_hit_at, hit_count, ttl_seconds, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (h, model, query, response, emb_bytes,
                 now, now, ttl, json.dumps(metadata or {}))
            )
            entry_id = cursor.lastrowid
        
        db.commit()
        return entry_id
    
    finally:
        db.close()

def stats() -> dict:
    """Get cache statistics."""
    db = get_db()
    try:
        total = db.execute("SELECT COUNT(*) as c FROM cache_entries").fetchone()["c"]
        expired = db.execute(
            "SELECT COUNT(*) as c FROM cache_entries WHERE created_at + ttl_seconds < ?",
            (time.time(),)
        ).fetchone()["c"]
        total_hits = db.execute(
            "SELECT COALESCE(SUM(hit_count), 0) as c FROM cache_entries"
        ).fetchone()["c"]
        has_emb = db.execute(
            "SELECT COUNT(*) as c FROM cache_entries WHERE embedding IS NOT NULL"
        ).fetchone()["c"]
        oldest = db.execute(
            "SELECT MIN(created_at) as c FROM cache_entries"
        ).fetchone()["c"]
        newest = db.execute(
            "SELECT MAX(created_at) as c FROM cache_entries"
        ).fetchone()["c"]
        
        return {
            "total_entries": total,
            "expired_entries": expired,
            "active_entries": total - expired,
            "total_hits": total_hits,
            "with_embedding": has_emb,
            "oldest_entry": oldest,
            "newest_entry": newest,
            "db_path": DB_PATH,
            "embed_model": "llama.cpp Q8_0" if USE_LLAMA_SERVER_DIRECT else EMBED_MODEL,
            "similarity_threshold": SIMILARITY_THRESHOLD
        }
    finally:
        db.close()

def vacuum():
    """Clean expired entries and vacuum the database."""
    db = get_db()
    try:
        now = time.time()
        cursor = db.execute(
            "DELETE FROM cache_entries WHERE created_at + ttl_seconds < ?",
            (now,)
        )
        deleted = cursor.rowcount
        db.commit()
        db.execute("VACUUM")
        return {"deleted": deleted}
    finally:
        db.close()

# === CLI Interface ===
def cli():
    """Simple CLI for testing."""
    if len(sys.argv) < 2:
        print("用法: python3 cache_pool.py <命令> [参数]")
        print()
        print("命令:")
        print("  search <query>         搜索缓存")
        print("  store <query> <resp>   存入缓存")
        print("  stats                  查看统计")
        print("  vacuum                 清理过期条目")
        return
    
    cmd = sys.argv[1]
    
    if cmd == "search":
        query = sys.argv[2] if len(sys.argv) > 2 else input("Query: ")
        result = search(query)
        if result["hit"]:
            print(f"✅ HIT ({result['match_type']})")
            if "similarity" in result:
                print(f"   相似度: {result['similarity']}")
            print(f"   命中次数: {result['hit_count']}")
            print(f"   响应: {result['response'][:200]}")
        else:
            print("❌ MISS")
    
    elif cmd == "store":
        query = sys.argv[2] if len(sys.argv) > 2 else input("Query: ")
        resp = sys.argv[3] if len(sys.argv) > 3 else input("Response: ")
        print("计算 embedding...")
        emb = get_embedding(query)
        entry_id = store(query, resp, embedding=emb)
        print(f"✅ 已缓存 (ID: {entry_id})")
    
    elif cmd == "stats":
        s = stats()
        print(f"📊 缓存统计")
        print(f"   总条目: {s['total_entries']}")
        print(f"   有效: {s['active_entries']}")
        print(f"   已过期: {s['expired_entries']}")
        print(f"   总命中: {s['total_hits']}")
        print(f"   带向量: {s['with_embedding']}")
        print(f"   数据库: {s['db_path']}")
        print(f"   模型: {s['embed_model']}")
        print(f"   阈值: {s['similarity_threshold']}")
    
    elif cmd == "vacuum":
        result = vacuum()
        print(f"🧹 已清理 {result['deleted']} 条过期条目")
    
    else:
        print(f"未知命令: {cmd}")

if __name__ == "__main__":
    cli()
