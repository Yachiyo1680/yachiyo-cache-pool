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

# Override embedding model to use lighter embeddinggemma
cp.EMBED_MODEL = "embeddinggemma:300m-qat-q4_0"

# === Configuration ===
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
PROXY_HOST = "0.0.0.0"  # Allow connections from localhost only is fine
PROXY_PORT = 18791
CACHE_NON_STREAMING = True  # Cache non-streaming responses
CACHE_STREAMING = False     # Pass through streaming without caching

app = Flask(__name__)

# DeepSeek API key - will be extracted from incoming requests
deepseek_api_key = None

def log(msg: str):
    """Simple log with timestamp."""
    print(f"[{time.strftime('%H:%M:%S')}] 🐙 {msg}", flush=True)

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
    # Don't cache streaming requests (unless configured)
    if body.get("stream", False) and not CACHE_STREAMING:
        return False
    
    # Don't cache if explicitly told not to
    if body.get("cache", {}).get("disable") == True:
        return False
    
    # Need at least one user message
    messages = body.get("messages", [])
    if not messages:
        return False
    
    return True

def build_response_from_cache(cached: dict, original_body: dict) -> tuple:
    """Build a proper HTTP response from cached data."""
    cached_response = json.loads(cached["response"])
    
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
    """Forward the request to actual DeepSeek API and return response."""
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
            # For streaming, return a streaming response
            def generate():
                for chunk in resp.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
            
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
        log("⚠️ DeepSeek API 超时！")
        return jsonify({
            "error": {"message": "Upstream request timed out", "type": "timeout"}
        }), 504
    
    except requests.exceptions.ConnectionError:
        log("❌ DeepSeek API 连接失败！")
        return jsonify({
            "error": {"message": "Cannot connect to DeepSeek API", "type": "connection_error"}
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
        
        # Extract the assistant's response text for caching
        choices = response_data.get("choices", [])
        if not choices:
            return
        
        assistant_content = choices[0].get("message", {}).get("content", "")
        if not assistant_content:
            return
        
        # The "query" for cache = the user's last message in plain text
        messages = body.get("messages", [])
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            return
        
        last_user_msg = user_messages[-1]
        query_text = last_user_msg.get("content", "")
        if isinstance(query_text, list):
            text_parts = [p.get("text", "") for p in query_text if p.get("type") == "text"]
            query_text = " ".join(text_parts)
        
        if not query_text.strip():
            return
        
        # Compute embedding and store using plain user text as query
        log(f"📥 缓存新回应 ({len(assistant_content)} chars)")
        emb = cp.get_embedding(query_text)
        cp.store(
            query=query_text,
            response=json.dumps(response_data, ensure_ascii=False),
            model=body.get("model", ""),
            embedding=emb,
            metadata={
                "system_context": include_system_context(messages)[:200],
                "model": body.get("model", ""),
                "cached_at": time.time()
            }
        )
    except Exception as e:
        log(f"⚠️ 缓存写入出错 (忽略): {e}")

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
    
    # Don't cache streaming requests (pass through)
    if body.get("stream", False):
        log("🔄 流式请求 → 直通转发")
        return forward_to_deepseek(headers, body)
    
    # Check if this is cacheable
    if not is_cacheable_request(body):
        log("⏭️ 不可缓存请求 → 直通转发")
        return forward_to_deepseek(headers, body)
    
    # Try cache lookup using last user message
    messages = body.get("messages", [])
    user_query = extract_last_user_message(messages)
    
    if user_query:
        try:
            cache_result = cp.search(user_query, model=body.get("model", ""))
        except Exception as e:
            log(f"⚠️ 缓存搜索出错 (直通转发): {e}")
            return forward_to_deepseek(headers, body)
        
        if cache_result["hit"]:
            match_type = cache_result.get("match_type", "unknown")
            match_info = cache_result.get("similarity", "")
            log(f"🎯 缓存命中! ({match_type})" + (f" sim={match_info}" if match_info else ""))
            return build_response_from_cache(cache_result, body)
    
    # Cache miss → forward to DeepSeek
    log("🔍 缓存未命中 → 请求 DeepSeek API")
    return forward_to_deepseek(headers, body)

@app.route("/v1/chat/completions", methods=["POST"])
def v1_chat_completions():
    """Also handle /v1/chat/completions path."""
    return chat_completions()

@app.route("/v1/embeddings", methods=["POST"])
def embeddings():
    """Forward embedding requests to DeepSeek (no caching for now)."""
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
    """Forward any other requests to DeepSeek."""
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
    log(f"🚀 缓存代理启动: http://{PROXY_HOST}:{PROXY_PORT}")
    log(f"   ➡️ 上游: {DEEPSEEK_BASE_URL}")
    log(f"   💾 缓存: {cp.DB_PATH}")
    log(f"   🧠 Embedding: {cp.EMBED_MODEL} (Ollama)")
    log(f"   🎯 相似度阈值: {cp.SIMILARITY_THRESHOLD}")
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
