#!/usr/bin/env python3
"""
🦞 Yachiyo's Cache Proxy 🐙
ローカルキャッシュプロキシ～☆

Transparent proxy that sits between OpenClaw and DeepSeek API.
Caches responses to save tokens and money!

Flow:
  OpenClaw → cache_proxy (localhost:18791) → DeepSeek API (api.deepseek.com)
                                ↓
                         Cache check → HIT? → return cached response
                                        → MISS? → forward to DeepSeek, cache & return
"""

import sys
import os
import json
import time
import threading
import requests
from flask import Flask, request, Response, jsonify
import urllib.parse

# Add parent directory for cache_pool import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_pool as cp

# Override embedding model from config
cp.EMBED_MODEL = cp.CFG["embedding"].get("ollama_model", "embeddinggemma:300m-qat-q4_0")

# === Configuration (从 config.yaml 读取) ===
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEEPSEEK_BASE_URL = cp.CFG["upstream"]["base_url"]
PROXY_HOST = cp.CFG["proxy"]["host"]
PROXY_PORT = cp.CFG["proxy"]["port"]
CACHE_NON_STREAMING = cp.CFG["proxy"]["cache_non_streaming"]
CACHE_STREAMING = cp.CFG["proxy"]["cache_streaming"]
LOG_FILE = os.path.join(SCRIPT_DIR, "cache_hit.log")

app = Flask(__name__)

# DeepSeek API key - will be extracted from incoming requests
deepseek_api_key = None

def log(msg: str):
    """Simple log with timestamp."""
    print(f"[{time.strftime('%H:%M:%S')}] 🐙 {msg}", flush=True)

def log_hit(match_type: str, query: str, similarity: float = None, token_saved: int = 0):
    """Append a structured cache hit/miss entry to the log file."""
    import csv
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    sim_str = f"{similarity:.4f}" if similarity else "N/A"
    token_str = str(token_saved) if match_type != "miss" else "0"
    line = f"[{now_str}] {match_type.upper():6s} | sim={sim_str} | saved=~{token_str} tokens | query={query[:120]} " + (f"| full_query={repr(query)}" if len(query) > 120 else "")
    
    # Write to log file (rotate if > 5MB)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
            base, ext = os.path.splitext(LOG_FILE)
            os.rename(LOG_FILE, f"{base}.old{ext}")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"[LOG ERROR] {e}", flush=True)

def extract_last_user_message(messages: list) -> str:
    """Extract the last user message as plain text for caching."""
    user_messages = [m for m in messages if m.get("role") == "user"]
    if not user_messages:
        return ""
    
    last = user_messages[-1]
    content = last.get("content", "")
    if isinstance(content, list):
        text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
        content = " ".join(text_parts)
    return content.strip()

def include_system_context(messages: list) -> str:
    """Include system prompt as context for better disambiguation."""
    system_msg = None
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                content = " ".join(text_parts)
            system_msg = content[:500]  # Keep system prompt brief
            break
    return system_msg or ""

def is_cacheable_request(body: dict) -> bool:
    """Check if this request is worth caching."""
    # Don't cache if explicitly told not to
    if body.get("cache", {}).get("disable") == True:
        return False
    
    # Need at least one user message
    messages = body.get("messages", [])
    if not messages:
        return False
    
    return True

def build_streaming_from_cache(cached: dict, original_body: dict) -> Response:
    """Build a streaming SSE response from cached data."""
    try:
        cached_response = json.loads(cached["response"])
        content = cached_response["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, TypeError, KeyError):
        content = str(cached.get("response", ""))
    
    model = original_body.get("model", "cached-model")
    now = int(time.time())
    match_type = str(cached.get("match_type", "hit"))
    
    def generate():
        # Send content in character chunks (works for both Chinese and English)
        chunk_size = 8  # chars per chunk for natural streaming feel
        for i in range(0, len(content), chunk_size):
            chunk_text = content[i:i + chunk_size]
            chunk = {
                "id": f"chatcmpl-cache-{now}",
                "object": "chat.completion.chunk",
                "created": now,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": chunk_text},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        
        # Done signal
        final = {
            "id": f"chatcmpl-cache-{now}",
            "object": "chat.completion.chunk",
            "created": now,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    
    return Response(
        generate(),
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Cache": match_type
        }
    )

def build_response_from_cache(cached: dict, original_body: dict) -> tuple:
    """Build a proper HTTP response from cached data."""
    try:
        cached_response = json.loads(cached["response"])
    except (json.JSONDecodeError, TypeError, KeyError):
        # Plain text cached response - wrap in OpenAI format
        resp_text = str(cached.get("response", ""))
        cached_response = {
            "id": f"chatcmpl-cache-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": original_body.get("model", "cached-model"),
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": resp_text
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }
        return jsonify(cached_response), 200, {"X-Cache": str(cached["match_type"])}
    
    # Update response id and created timestamp for freshness
    cached_response["id"] = f"chatcmpl-cache-{int(time.time())}"
    cached_response["created"] = int(time.time())
    cached_response["model"] = original_body.get("model", cached_response.get("model", "unknown"))
    
    # Set usage to 0 cost for cached responses
    cached_response["usage"] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "completion_tokens_details": {"cached_tokens": True}
    }
    
    return jsonify(cached_response), 200, {"X-Cache": str(cached["match_type"])}

def forward_to_deepseek(headers: dict, body: dict) -> Response:
    """Forward the request to actual upstream API and return response."""
    target_url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    
    # Forward the request
    forward_headers = {
        "Content-Type": "application/json",
        "Authorization": headers.get("Authorization", f"Bearer {deepseek_api_key}"),
    }
    
    try:
        resp = requests.post(
            target_url,
            headers=forward_headers,
            json=body,
            stream=body.get("stream", False),
            timeout=120
        )
        
        if body.get("stream", False):
            # For streaming: proxy chunks to client AND buffer for caching
            collected_content = ""
            
            def generate():
                nonlocal collected_content
                for chunk in resp.iter_content(chunk_size=None):
                    if chunk:
                        chunk_str = chunk.decode("utf-8", errors="replace")
                        # Extract content from SSE format
                        for line in chunk_str.split("\n"):
                            if line.startswith("data: ") and line != "data: [DONE]":
                                try:
                                    data = json.loads(line[6:])
                                    choices = data.get("choices", [])
                                    if choices:
                                        delta = choices[0].get("delta", {})
                                        content = delta.get("content", "")
                                        if content:
                                            collected_content += content
                                except json.JSONDecodeError:
                                    pass
                        yield chunk
                
                # After streaming completes, cache the full response
                if collected_content:
                    response_data = {
                        "id": f"chatcmpl-{int(time.time())}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": body.get("model", ""),
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": collected_content},
                            "finish_reason": "stop"
                        }],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                    }
                    try:
                        _try_cache_response(body, response_data)
                        log(f"📥 流式缓存已保存 ({len(collected_content)} chars)")
                    except Exception as e:
                        log(f"⚠️ 流式缓存写入失败: {e}")
            
            return Response(
                generate(),
                status=resp.status_code,
                headers={
                    "Content-Type": resp.headers.get("Content-Type", "text/event-stream"),
                    "X-Cache": "miss-stream"
                }
            )
        else:
            # Non-streaming: parse, cache, return
            response_data = resp.json()
            
            # Try to cache it
            try:
                _try_cache_response(body, response_data)
            except Exception as e:
                log(f"⚠️ 缓存写入失败 (不影响请求): {e}")
            
            flask_resp = jsonify(response_data)
            flask_resp.headers["X-Cache"] = "miss"
            flask_resp.status_code = resp.status_code
            return flask_resp
    
    except requests.exceptions.Timeout:
        log(f"⚠️ 上游 API 超时！({DEEPSEEK_BASE_URL})")
        return jsonify({
            "error": {"message": "Upstream request timed out", "type": "timeout"}
        }), 504
    
    except requests.exceptions.ConnectionError:
        log(f"❌ 上游 API 连接失败！({DEEPSEEK_BASE_URL})")
        return jsonify({
            "error": {"message": f"Cannot connect to upstream ({DEEPSEEK_BASE_URL})", "type": "connection_error"}
        }), 502
    
    except Exception as e:
        log(f"❌ 转发请求出错: {e}")
        return jsonify({
            "error": {"message": f"Proxy error: {str(e)}", "type": "proxy_error"}
        }), 500

def _try_cache_response(body: dict, response_data: dict):
    """Attempt to cache a response. Silently fails if anything goes wrong."""
    try:
        if not is_cacheable_request(body):
            return
        
        choices = response_data.get("choices", [])
        if not choices:
            return
        
        message = choices[0].get("message", {})
        model = body.get("model", "")
        messages = body.get("messages", [])
        user_query = extract_last_user_message(messages)
        if not user_query.strip():
            return
        
        # === Case 1: Response contains tool_calls -> 存入工具缓存 ===
        tool_calls = message.get("tool_calls")
        if tool_calls:
            tool_names = [tc["function"]["name"] for tc in tool_calls]
            log(f"🔧 缓存 tool_call: {len(tool_calls)} 个工具 ({tool_names})")
            cp.store_tool(
                query=user_query,
                tool_calls=tool_calls,
                model=model,
                ttl=3600
            )
            return
        
        # === Case 2: Normal content response -> 存入语义缓存 ===
        assistant_content = message.get("content", "")
        if not assistant_content:
            return
        
        log(f"📥 缓存新回应 ({len(assistant_content)} chars)")
        emb = cp.get_embedding(user_query)
        cp.store(
            query=user_query,
            response=json.dumps(response_data, ensure_ascii=False),
            model=model,
            embedding=emb,
            metadata={
                "system_context": include_system_context(messages)[:200],
                "model": model,
                "cached_at": time.time()
            }
        )
    except Exception as e:
        log(f"⚠️ 缓存写入出错 (忽略): {e}")

@app.route("/v1/models", methods=["GET"])
def list_models():
    """Return available models (for Open WebUI compatibility)."""
    return jsonify({
        "object": "list",
        "data": [
            {"id": "deepseek-v4-flash", "object": "model"},
            {"id": "deepseek-v4-pro", "object": "model"},
            {"id": "gpt-5.4-mini", "object": "model"},
            {"id": "gpt-5.6-luna", "object": "model"},
        ]
    })

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "cache_stats": cp.stats(),
        "mode": "proxy"
    })

@app.route("/chat/completions", methods=["POST"])
def chat_completions():
    """Main proxy endpoint."""
    global deepseek_api_key
    
    body = request.get_json(silent=True) or {}
    headers = dict(request.headers)
    
    # Store API key from request
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        deepseek_api_key = auth[7:]
    
    is_stream = body.get("stream", False)
    messages = body.get("messages", [])
    user_query = extract_last_user_message(messages)
    
    # === Step 1: Try Tool Cache first (精确匹配, 无 embedding) ===
    if user_query and is_cacheable_request(body):
        try:
            tool_cache_result = cp.search_tool(user_query, model=body.get("model", ""))
            if tool_cache_result["hit"]:
                tool_calls = tool_cache_result["tool_calls"]
                tool_names = [tc["function"]["name"] for tc in tool_calls]
                # 估算省掉的 token：整个请求体大小 / 2 chars-per-token
                body_chars = len(json.dumps(body, ensure_ascii=False))
                estimated_saved = max(body_chars // 2, 100)
                log(f"🔧 工具缓存命中! 🐙 {tool_names} saved=~{estimated_saved}tokens")
                log_hit("tool", user_query, token_saved=estimated_saved)
                return build_tool_response(tool_cache_result, body)
        except Exception as e:
            log(f"⚠️ 工具缓存搜索出错: {e}")
    
    # === Step 2: Try Semantic Cache next ===
    cache_hit = False
    cache_result = None
    
    if user_query and is_cacheable_request(body):
        try:
            cache_result = cp.search(user_query, model=body.get("model", ""))
            cache_hit = cache_result["hit"]
        except Exception as e:
            log(f"⚠️ 语义缓存搜索出错: {e}")
    
    if cache_hit:
        match_type = cache_result.get("match_type", "unknown")
        match_info = cache_result.get("similarity", "")
        resp_text = cache_result.get("response", "")
        token_saved = len(resp_text) // 2 if resp_text else 0
        log_info = f" sim={match_info}" if match_info else ""
        log(f"🎯 语义缓存命中! ({match_type})" + log_info + f" saved=~{token_saved}tokens")
        log_hit(match_type, user_query, match_info or None, token_saved)
        
        if is_stream:
            return build_streaming_from_cache(cache_result, body)
        else:
            return build_response_from_cache(cache_result, body)
    
    # === Step 3: Cache miss -> forward to upstream ===
    log("🔍 缓存未命中 → 请求" + (" (流式)" if is_stream else ""))
    log_hit("miss", user_query)
    return forward_to_deepseek(headers, body)

def build_tool_response(cached: dict, original_body: dict) -> tuple:
    """Build a proper HTTP response from cached tool_calls."""
    now = int(time.time())
    response = {
        "id": f"chatcmpl-tool-{now}",
        "object": "chat.completion",
        "created": now,
        "model": original_body.get("model", "cached-model"),
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": cached["tool_calls"]
            },
            "finish_reason": "tool_calls"
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
    }
    
    return jsonify(response), 200, {
        "X-Cache": "tool",
        "X-Cache-Hits": str(cached.get("hit_count", 1))
    }

@app.route("/v1/chat/completions", methods=["POST"])
def v1_chat_completions():
    """Also handle /v1/chat/completions path."""
    return chat_completions()

@app.route("/v1/embeddings", methods=["POST"])
def embeddings():
    """Forward embedding requests to upstream (no caching for now)."""
    global deepseek_api_key
    headers = dict(request.headers)
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        deepseek_api_key = auth[7:]
    
    body = request.get_json(silent=True) or {}
    target_url = f"{DEEPSEEK_BASE_URL}/v1/embeddings"
    
    forward_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {deepseek_api_key}",
    }
    
    try:
        resp = requests.post(target_url, headers=forward_headers, json=body, timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def catch_all(path):
    """Forward any other requests to upstream."""
    global deepseek_api_key
    headers = dict(request.headers)
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        deepseek_api_key = auth[7:]
    
    target_url = f"{DEEPSEEK_BASE_URL}/{path}"
    
    forward_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {deepseek_api_key}",
    }
    
    try:
        body = request.get_json(silent=True) if request.method in ("POST", "PUT") else None
        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=forward_headers,
            json=body,
            timeout=30
        )
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def run():
    """Start the proxy server."""
    config_path = os.path.join(SCRIPT_DIR, "config.yaml")
    log(f"🚀 缓存代理启动: http://{PROXY_HOST}:{PROXY_PORT}")
    log(f"   ➡️ 上游: {DEEPSEEK_BASE_URL}")
    log(f"   💾 缓存: {cp.DB_PATH}")
    log(f"   ⚙️ 配置: {config_path}")
    if cp.USE_LLAMA_SERVER_DIRECT:
        log(f"   🧠 Embedding: llama.cpp server ({cp.LLAMA_SERVER_URL}, Q8_0)")
    else:
        log(f"   🧠 Embedding: {cp.EMBED_MODEL} (Ollama)")
    log(f"   🎯 相似度阈值: {cp.SIMILARITY_THRESHOLD}")
    log(f"   📊 命中日志: {LOG_FILE}")
    log(f"   ⏱️ TTL: {cp.DEFAULT_TTL // 86400} 天")
    log("")
    log(f"📋 使用说明: 将 OpenClaw 配置中的 baseUrl 改为")
    log(f"   http://localhost:{PROXY_PORT} 即可启用")
    log(f"   (原 API Key 会自动透传)")
    log("")
    
    # Use Flask's built-in server (serving purpose)
    from werkzeug.serving import WSGIRequestHandler
    WSGIRequestHandler.protocol_version = "HTTP/1.1"
    
    app.run(
        host=PROXY_HOST,
        port=PROXY_PORT,
        debug=False,
        threaded=True,
        use_reloader=False
    )

if __name__ == "__main__":
    run()
