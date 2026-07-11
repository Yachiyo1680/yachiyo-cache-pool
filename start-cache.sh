#!/bin/bash
# 🐙 一键启动 llama-server + 缓存代理（开包即用版）
# Yachiyo's Cache Pool - 克隆下来 bash start-cache.sh 就能跑

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LLAMA_SERVER_BIN="$SCRIPT_DIR/bin/llama-server"
MODEL_DIR="$SCRIPT_DIR/models"
GGUF="$MODEL_DIR/embeddinggemma-300m-qat-Q8_0.gguf"
PYTHON_DEPS=("flask" "requests")

echo "🐙 ヤチヨのキャッシュプロキシ～☆"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━"

# === 1. 检查/安装 Python 依赖 ===
echo "📦 检查 Python 依赖..."
MISSING_DEPS=false
for dep in "${PYTHON_DEPS[@]}"; do
    if ! python3 -c "import $dep" 2>/dev/null; then
        MISSING_DEPS=true
        break
    fi
done

if $MISSING_DEPS; then
    echo "   → 安装 Python 依赖..."
    pip3 install -r "$SCRIPT_DIR/requirements.txt" --break-system-packages -q 2>/dev/null || \
    pip3 install flask requests --break-system-packages -q
fi
echo "   ✅ Python 依赖已就绪"

# === 2. 检查/下载 llama-server ===
echo "🔧 检查 llama-server..."
if [ ! -f "$LLAMA_SERVER_BIN" ]; then
    echo "   → 未找到本地 llama-server，尝试自动下载..."
    mkdir -p "$SCRIPT_DIR/bin"
    
    # 检测架构
    ARCH=$(uname -m)
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        BINARY="llama-server-$OS-aarch64"
    elif [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then
        BINARY="llama-server-$OS-x86_64"
    else
        echo "   ⚠️ 架构 $ARCH 没有预编译的 llama-server 二进制"
        echo "   尝试从系统寻找..."
        # Fallback: check common locations
        for loc in /usr/local/lib/ollama/llama-server /usr/bin/llama-server /usr/local/bin/llama-server; do
            if [ -f "$loc" ]; then
                echo "   → 使用 $loc"
                LLAMA_SERVER_BIN="$loc"
                break
            fi
        done
        if [ ! -f "$LLAMA_SERVER_BIN" ]; then
            echo "❌ 找不到 llama-server，请手动编译:"
            echo "   git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp && make llama-server"
            echo "   然后把二进制放到 $SCRIPT_DIR/bin/"
            exit 1
        fi
    fi
    
    if [ "$LLAMA_SERVER_BIN" = "$SCRIPT_DIR/bin/llama-server" ]; then
        echo "   → 下载 $BINARY ..."
        DOWNLOAD_URL="https://github.com/ggml-org/llama.cpp/releases/latest/download/$BINARY"
        HTTP_CODE=$(curl -sL -o "$LLAMA_SERVER_BIN" -w "%{http_code}" "$DOWNLOAD_URL" 2>/dev/null || echo "000")
        
        if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ]; then
            chmod +x "$LLAMA_SERVER_BIN"
            echo "   ✅ llama-server 下载成功"
        else
            rm -f "$LLAMA_SERVER_BIN"
            echo "   ⚠️ 预编译二进制下载失败 (HTTP $HTTP_CODE)"
            echo "   尝试从系统寻找..."
            for loc in /usr/local/lib/ollama/llama-server /usr/bin/llama-server /usr/local/bin/llama-server; do
                if [ -f "$loc" ]; then
                    echo "   → 使用 $loc"
                    cp "$loc" "$LLAMA_SERVER_BIN"
                    chmod +x "$LLAMA_SERVER_BIN"
                    break
                fi
            done
            if [ ! -f "$LLAMA_SERVER_BIN" ]; then
                echo "❌ 无法获取 llama-server。你可以:"
                echo "   1. 手动下载: https://github.com/ggml-org/llama.cpp/releases"
                echo "   2. 或用 Ollama 的 embed 功能（改 cache_pool.py 的 USE_LLAMA_SERVER_DIRECT = False）"
                exit 1
            fi
        fi
    fi
else
    echo "   ✅ 本地 llama-server 已就绪"
fi

# === 3. 检查/下载 GGUF 模型 ===
echo "🧠 检查 embedding 模型..."
if [ ! -f "$GGUF" ]; then
    echo "   → 未找到本地模型，自动下载 (~314MB)..."
    mkdir -p "$MODEL_DIR"
    
    # Try HuggingFace download
    HF_URL="https://huggingface.co/ggml-org/embeddinggemma-300m-qat/resolve/main/embeddinggemma-300m-qat-Q8_0.gguf"
    echo "   ⬇️  下载中 (这可能需要几分钟)..."
    
    # Use curl with progress bar
    if command -v wget &>/dev/null; then
        wget -q --show-progress "$HF_URL" -O "$GGUF" 2>&1
    else
        curl -sL -o "$GGUF" "$HF_URL" 2>&1
    fi
    
    if [ -f "$GGUF" ] && [ -s "$GGUF" ]; then
        echo "   ✅ 模型下载成功 ($(du -h "$GGUF" | cut -f1))"
    else
        rm -f "$GGUF"
        echo "❌ 下载失败。请手动下载:"
        echo "   $HF_URL"
        echo "   放到 $GGUF"
        exit 1
    fi
else
    echo "   ✅ 模型已就绪 ($(du -h "$GGUF" | cut -f1))"
fi

# === 4. 启动 llama-server ===
if curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo "✅ llama.cpp server (端口 8080) 已在运行中"
else
    echo "🚀 启动 llama.cpp embedding server..."
    nohup "$LLAMA_SERVER_BIN" \
        --model "$GGUF" --port 8080 --host 127.0.0.1 \
        --embedding --ctx-size 512 --batch-size 512 \
        --threads 4 --no-webui --no-mmap \
        --log-disable --pooling mean --embd-normalize 2 \
        > /dev/null 2>&1 &
    LLAMA_PID=$!
    sleep 3
    
    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
        echo "   ✅ PID: $LLAMA_PID"
    else
        # Try once more with longer wait
        sleep 3
        if curl -s http://localhost:8080/health > /dev/null 2>&1; then
            echo "   ✅ PID: $LLAMA_PID"
        else
            echo "❌ llama-server 启动失败"
            exit 1
        fi
    fi
fi

# === 5. 启动缓存代理 ===
if curl -s http://localhost:18791/health > /dev/null 2>&1; then
    echo "✅ 缓存代理 (端口 18791) 已在运行中"
else
    echo "🚀 启动缓存代理..."
    nohup python3 "$SCRIPT_DIR/cache_proxy.py" > /tmp/cache_proxy.log 2>&1 &
    sleep 3
    if curl -s http://localhost:18791/health > /dev/null 2>&1; then
        echo "   ✅ PID: $(curl -s http://localhost:18791/health 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get(\"status\",\"ok\"))' 2>/dev/null || echo 'running')"
    else
        echo "❌ 代理启动失败，看日志: tail /tmp/cache_proxy.log"
        exit 1
    fi
fi

# === 6. 完成 ===
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎉 全部就绪！"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "   🧠 Embedding:  http://localhost:8080/v1/embeddings (Q8_0)"
echo "   🚀 缓存代理:   http://localhost:18791"
echo "   💾 数据库:     $SCRIPT_DIR/cache.db"
echo "   📊 命中日志:   $SCRIPT_DIR/cache_hit.log"
echo ""
echo "📋 使用说明:"
echo "   将任何 OpenAI 兼容客户端的 baseUrl 改为:"
echo "   http://localhost:18791"
echo "   API Key 会自动透传"
echo ""
echo "🔍 查看日志:   tail -f /tmp/cache_proxy.log"
echo "📊 缓存统计:   curl -s http://localhost:18791/health | python3 -m json.tool"
echo "🔬 测试搜索:   cd $SCRIPT_DIR && bash cache.sh search '试试'"
echo ""
echo "🐙 感謝 感激 雨アラモード～☆"
