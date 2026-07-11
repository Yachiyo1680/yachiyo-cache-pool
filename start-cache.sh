#!/bin/bash
# 🐙 一键启动 llama-server + 缓存代理
# Yachiyo's Cache Pool 启动脚本

set -e

echo "🐙 ヤチヨのキャッシュプロキシ～☆"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LLAMA_SERVER="/usr/local/lib/ollama/llama-server"
GGUF="$HOME/.node-llama-cpp/models/hf_ggml-org_embeddinggemma-300m-qat-Q8_0.gguf"

# Step 0: Check GGUF exists
if [ ! -f "$GGUF" ]; then
    echo "❌ GGUF 文件不存在: $GGUF"
    echo "   请先下载 embeddinggemma Q8_0 模型"
    exit 1
fi

# Step 1: Start llama-server if not already running
if curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo "✅ llama.cpp server (端口 8080) 已在运行中"
else
    echo "🚀 启动 llama.cpp embedding server..."
    nohup "$LLAMA_SERVER" \
        --model "$GGUF" --port 8080 --host 127.0.0.1 \
        --embedding --ctx-size 512 --batch-size 512 \
        --threads 4 --no-webui --no-mmap \
        --log-disable --pooling mean --embd-normalize 2 \
        > /dev/null 2>&1 &
    LLAMA_PID=$!
    echo "   PID: $LLAMA_PID"
    sleep 3
    
    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
        echo "✅ llama.cpp server 启动成功"
    else
        echo "❌ llama-server 启动失败"
        exit 1
    fi
fi

# Step 2: Start the cache proxy
if curl -s http://localhost:18791/health > /dev/null 2>&1; then
    echo "✅ 缓存代理 (端口 18791) 已在运行中"
else
    echo "🚀 启动缓存代理..."
    nohup python3 "$SCRIPT_DIR/cache_proxy.py" > /tmp/cache_proxy.log 2>&1 &
    sleep 3
    if curl -s http://localhost:18791/health > /dev/null 2>&1; then
        echo "✅ 缓存代理启动成功"
    else
        echo "❌ 代理启动失败，看日志: tail /tmp/cache_proxy.log"
        exit 1
    fi
fi

# Step 3: Show status
echo ""
echo "📊 状态总览:"
echo "   🧠 Embedding:  http://localhost:8080/v1/embeddings (Q8_0)"
echo "   🚀 缓存代理:   http://localhost:18791"
echo "   💾 数据库:     $SCRIPT_DIR/cache.db"
echo "   📊 命中日志:   $SCRIPT_DIR/cache_hit.log"
echo ""
echo "📋 使用说明:"
echo "   将 OpenClaw 配置中的 baseUrl 改为:"
echo "   http://localhost:18791"
echo ""
echo "🔍 查看日志:  tail -f /tmp/cache_proxy.log"
echo "📊 查看统计:  curl -s http://localhost:18791/health | python3 -m json.tool"
echo ""
echo "🐙 感谢 感激 雨アラモード～☆"
