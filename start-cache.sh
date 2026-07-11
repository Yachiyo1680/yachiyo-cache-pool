#!/bin/bash
# 🐙 一键启动 llama-server + 缓存代理（开包即用版）
# Yachiyo's Cache Pool - 克隆下来 bash start-cache.sh 就能跑

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LLAMA_SERVER_BIN="$SCRIPT_DIR/bin/llama-server"
MODEL_DIR="$SCRIPT_DIR/models"
GGUF="$MODEL_DIR/embeddinggemma-300M-Q8_0.gguf"
GGUF_URL="https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/resolve/main/embeddinggemma-300M-Q8_0.gguf"
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
    # Try with --break-system-packages first (Debian/Ubuntu), fallback to normal
    pip3 install -r "$SCRIPT_DIR/requirements.txt" --break-system-packages -q 2>/dev/null || \
    pip3 install -r "$SCRIPT_DIR/requirements.txt" -q 2>/dev/null || \
    pip3 install flask requests pyyaml --break-system-packages -q 2>/dev/null || \
    pip3 install flask requests pyyaml -q
fi
echo "   ✅ Python 依赖已就绪"

# === 2. 检查/下载 llama-server ===
echo "🔧 检查 llama-server..."
if [ ! -f "$LLAMA_SERVER_BIN" ]; then
    echo "   → 未找到本地 llama-server，尝试自动下载..."
    mkdir -p "$SCRIPT_DIR/bin"
    
    # 检测架构和系统，确定要下载的包名
    ARCH=$(uname -m)
    
    # 动态获取最新版本号
    echo "   → 查询最新版本..."
    LLAMA_TAG=$(curl -sL "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tag_name',''))" 2>/dev/null)
    if [ -z "$LLAMA_TAG" ]; then
        echo "   ⚠️ 无法获取最新版本号，使用默认 b9964"
        LLAMA_TAG="b9964"
    fi
    echo "   → 版本: $LLAMA_TAG"
    
    case "$ARCH" in
        aarch64|arm64)
            if [ "$(uname -s)" = "Darwin" ]; then
                PKG="llama.cpp/releases/download/$LLAMA_TAG/llama-${LLAMA_TAG}-bin-macos-arm64.tar.gz"
            else
                PKG="llama.cpp/releases/download/$LLAMA_TAG/llama-${LLAMA_TAG}-bin-ubuntu-arm64.tar.gz"
            fi
            ;;
        x86_64|amd64)
            if [ "$(uname -s)" = "Darwin" ]; then
                PKG="llama.cpp/releases/download/$LLAMA_TAG/llama-${LLAMA_TAG}-bin-macos-x64.tar.gz"
            else
                PKG="llama.cpp/releases/download/$LLAMA_TAG/llama-${LLAMA_TAG}-bin-ubuntu-x64.tar.gz"
            fi
            ;;
        *)
            echo "   ⚠️ 架构 $ARCH 没有预编译的 llama-server"
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
                echo "❌ 找不到 llama-server。可以:"
                echo "   1. 从 GitHub Releases 手动下载: https://github.com/ggml-org/llama.cpp/releases"
                echo "   2. 或改用 Ollama（编辑 config.yaml 设置 backend: ollama）"
                exit 1
            fi
            ;;
    esac
    
    if [ -n "$PKG" ]; then
        DOWNLOAD_URL="https://github.com/ggml-org/$PKG"
        PKG_SIZE="$(basename $PKG) (~15MB)"
        echo "   → 下载 $PKG_SIZE ..."
        echo "     从 GitHub Releases 下载，国内可能稍慢，请稍候..."
        TMP_TAR="/tmp/llama-server.tar.gz"
        # 显示进度条下载
        echo "     (若长时间无响应可按 Ctrl+C 取消)"
        if command -v curl &>/dev/null; then
            curl -L --progress-bar -o "$TMP_TAR" "$DOWNLOAD_URL"
            HTTP_CODE="$?"
            [ "$HTTP_CODE" = "0" ] && HTTP_CODE="200" || HTTP_CODE="000"
        else
            wget --no-check-certificate --show-progress -O "$TMP_TAR" "$DOWNLOAD_URL"
            HTTP_CODE="$?"
            [ "$HTTP_CODE" = "0" ] && HTTP_CODE="200" || HTTP_CODE="000"
        fi
        
        if [ "$HTTP_CODE" = "200" ]; then
            # 提取 llama-server 二进制（解压全部文件，然后保留需要的）
            tar xzf "$TMP_TAR" -C "$SCRIPT_DIR/bin" --strip-components 1 2>/dev/null
            rm -f "$TMP_TAR"
            if [ -f "$LLAMA_SERVER_BIN" ]; then
                chmod +x "$LLAMA_SERVER_BIN"
                echo "   ✅ llama-server 下载成功 ($(du -h "$LLAMA_SERVER_BIN" | cut -f1))"
            else
                # 再试试用 find 找一下
                FOUND=$(find "$SCRIPT_DIR/bin" -name "llama-server" -type f 2>/dev/null | head -1)
                if [ -n "$FOUND" ]; then
                    mv "$FOUND" "$LLAMA_SERVER_BIN"
                    chmod +x "$LLAMA_SERVER_BIN"
                    echo "   ✅ llama-server 解压成功"
                else
                    echo "   ⚠️ 下载包可能不完整，删除重试..."
                    rm -rf "$SCRIPT_DIR/bin/"*
                    echo "   ❌ 下载失败。可从 GitHub 手动下载:"
                    echo "      $DOWNLOAD_URL"
                    echo "     然后解压出 llama-server 放到 $SCRIPT_DIR/bin/"
                    echo "   🔄 或改用 Ollama 后端（编辑 config.yaml 设置 backend: ollama）"
                    exit 1
                fi
            fi
        else
            rm -f "$TMP_TAR"
            echo "   ⚠️ 下载失败 (HTTP $HTTP_CODE)，尝试从系统寻找..."
            for loc in /usr/local/lib/ollama/llama-server /usr/bin/llama-server /usr/local/bin/llama-server; do
                if [ -f "$loc" ]; then
                    cp "$loc" "$LLAMA_SERVER_BIN"
                    chmod +x "$LLAMA_SERVER_BIN"
                    echo "   → 使用 $loc"
                    break
                fi
            done
            if [ ! -f "$LLAMA_SERVER_BIN" ]; then
                echo "❌ 无法获取 llama-server。可以:"
                echo "   1. 从 GitHub Releases 手动下载: https://github.com/ggml-org/llama.cpp/releases"
                echo "   2. 或改用 Ollama（编辑 config.yaml 设置 backend: ollama）"
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
    HF_URL="$GGUF_URL"
    echo "   ⬇️  下载 embeddinggemma-300M-Q8_0 (~314MB) ..."
    echo "     从 HuggingFace 下载，国内可能较慢，耐心等待..."
    echo "     (若长时间无进度可按 Ctrl+C 取消)"
    
    if command -v curl &>/dev/null; then
        # curl 的进度条最可靠
        curl -L --progress-bar -o "$GGUF" "$HF_URL"
    else
        wget --no-check-certificate --show-progress -O "$GGUF" "$HF_URL"
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
