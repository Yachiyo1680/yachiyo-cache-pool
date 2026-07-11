#!/bin/bash
# 🐙 一键启动 llama-server + 缓存代理（开包即用版）
# Yachiyo's Cache Pool - 克隆下来 bash start-cache.sh 就能跑

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LLAMA_SERVER_BIN="$SCRIPT_DIR/bin/llama-server"
MODEL_DIR="$SCRIPT_DIR/models"
GGUF="$MODEL_DIR/embeddinggemma-300M-Q8_0.gguf"
GGUF_URL="https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/resolve/main/embeddinggemma-300M-Q8_0.gguf"
PYTHON_DEPS=("flask" "requests" "yaml")

echo ""
echo "🐙 ヤチヨのキャッシュプロキシ～☆"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# === 1. 检查/安装 Python 依赖 ===
echo "[1/5] 📦 Python 依赖"
for dep in "${PYTHON_DEPS[@]}"; do
    if python3 -c "import $dep" 2>&1; then
        echo "  ✅ $dep 已安装"
    else
        echo "  → 安装 $dep ..."
        pip3 install -r "$SCRIPT_DIR/requirements.txt" --break-system-packages 2>&1 || \
        pip3 install -r "$SCRIPT_DIR/requirements.txt" 2>&1 || \
        pip3 install flask requests pyyaml --break-system-packages 2>&1 || \
        pip3 install flask requests pyyaml 2>&1
        break
    fi
done

# === 2. 检查/下载 llama-server ===
echo ""
echo "[2/5] 🔧 llama-server"

if [ -f "$LLAMA_SERVER_BIN" ]; then
    echo "  ✅ 已就绪 ($(du -h "$LLAMA_SERVER_BIN" | cut -f1))"
else
    echo "  → 未找到，开始自动下载..."
    mkdir -p "$SCRIPT_DIR/bin"
    
    # 检测架构
    ARCH=$(uname -m)
    echo "  → 架构: $ARCH"
    
    # 动态获取最新版本号
    echo "  → 查询最新版本..."
    LLAMA_TAG=$(curl -sL "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tag_name',''))" 2>&1)
    if [ -z "$LLAMA_TAG" ]; then
        echo "  ⚠️ 无法获取最新版本号，使用默认 b9964"
        LLAMA_TAG="b9964"
    fi
    echo "  → 版本: $LLAMA_TAG"
    
    # 选择正确的包
    case "$ARCH" in
        aarch64|arm64)
            [ "$(uname -s)" = "Darwin" ] && OS_SUFFIX="macos-arm64" || OS_SUFFIX="ubuntu-arm64"
            ;;
        x86_64|amd64)
            [ "$(uname -s)" = "Darwin" ] && OS_SUFFIX="macos-x64" || OS_SUFFIX="ubuntu-x64"
            ;;
        *)
            echo "  ❌ 不支持的架构: $ARCH"
            echo "  可改用 Ollama 后端（编辑 config.yaml 设置 backend: ollama）"
            echo "  或手动下载: https://github.com/ggml-org/llama.cpp/releases"
            exit 1
            ;;
    esac
    
    PKG="llama.cpp/releases/download/$LLAMA_TAG/llama-${LLAMA_TAG}-bin-${OS_SUFFIX}.tar.gz"
    DOWNLOAD_URL="https://github.com/ggml-org/$PKG"
    TMP_TAR="/tmp/llama-server.tar.gz"
    
    echo "  → 下载 $(basename $PKG) (~15MB) ..."
    echo "  (从 GitHub Releases 下载，国内可能稍慢)"
    echo ""
    
    curl -L --progress-bar -o "$TMP_TAR" "$DOWNLOAD_URL"
    echo ""
    echo "  → 下载完成，解压中..."
    
    tar xzf "$TMP_TAR" -C "$SCRIPT_DIR/bin" --strip-components 1
    
    if [ -f "$LLAMA_SERVER_BIN" ]; then
        chmod +x "$LLAMA_SERVER_BIN"
        rm -f "$TMP_TAR"
        echo "  ✅ llama-server 就绪 ($(du -h "$LLAMA_SERVER_BIN" | cut -f1))"
    else
        echo "  ⚠️ 解压后找不到 llama-server，搜索中..."
        FOUND=$(find "$SCRIPT_DIR/bin" -name "llama-server" -type f | head -1)
        if [ -n "$FOUND" ]; then
            mv "$FOUND" "$LLAMA_SERVER_BIN"
            chmod +x "$LLAMA_SERVER_BIN"
            rm -f "$TMP_TAR"
            echo "  ✅ llama-server 就绪"
        else
            rm -rf "$SCRIPT_DIR/bin/"*
            rm -f "$TMP_TAR"
            echo "  ❌ 下载失败或文件不完整"
            echo "  可改用 Ollama 后端（编辑 config.yaml 设置 backend: ollama）"
            exit 1
        fi
    fi
fi

# === 3. 检查/下载 GGUF 模型 ===
echo ""
echo "[3/5] 🧠 Embedding 模型"

if [ -f "$GGUF" ]; then
    echo "  ✅ 已就绪 ($(du -h "$GGUF" | cut -f1))"
else
    echo "  → 未找到，自动下载 (~314MB) ..."
    mkdir -p "$MODEL_DIR"
    echo "  (从 HuggingFace 下载，国内可能较慢)"
    echo ""
    
    curl -L --progress-bar -o "$GGUF" "$GGUF_URL"
    echo ""
    
    if [ -f "$GGUF" ] && [ -s "$GGUF" ]; then
        echo "  ✅ 模型就绪 ($(du -h "$GGUF" | cut -f1))"
    else
        rm -f "$GGUF"
        echo "  ❌ 下载失败"
        echo "  可手动下载后放到: $GGUF"
        echo "  下载地址: $GGUF_URL"
        exit 1
    fi
fi

# === 4. 启动 llama-server ===
echo ""
echo "[4/5] 🚀 启动服务"

if curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo "  ✅ llama-server (端口 8080) 已在运行"
else
    echo "  → 启动 llama-server ..."
    nohup "$LLAMA_SERVER_BIN" \
        --model "$GGUF" --port 8080 --host 127.0.0.1 \
        --embedding --ctx-size 512 --batch-size 512 \
        --threads 4 --no-webui --no-mmap \
        --log-disable --pooling mean --embd-normalize 2 \
        > /tmp/llama-server.log 2>&1 &
    
    echo "  → 等待启动... (约 5 秒)"
    sleep 3
    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
        echo "  ✅ llama-server 已启动"
    else
        sleep 3
        if curl -s http://localhost:8080/health > /dev/null 2>&1; then
            echo "  ✅ llama-server 已启动"
        else
            echo "  ❌ 启动失败，日志:"
            cat /tmp/llama-server.log 2>/dev/null | tail -10
            exit 1
        fi
    fi
fi

# === 5. 启动缓存代理 ===
if curl -s http://localhost:18791/health > /dev/null 2>&1; then
    echo "  ✅ 缓存代理 (端口 18791) 已在运行"
else
    echo "  → 启动缓存代理..."
    nohup python3 "$SCRIPT_DIR/cache_proxy.py" > /tmp/cache_proxy.log 2>&1 &
    sleep 3
    if curl -s http://localhost:18791/health > /dev/null 2>&1; then
        echo "  ✅ 缓存代理已启动"
    else
        echo "  ❌ 启动失败，日志:"
        cat /tmp/cache_proxy.log 2>/dev/null | tail -10
        exit 1
    fi
fi

# === 完成 ===
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🎉 全部就绪！"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📍 Embedding:  http://localhost:8080/v1/embeddings"
echo "  📍 缓存代理:   http://localhost:18791"
echo "  💾 数据库:     $SCRIPT_DIR/cache.db"
echo ""
echo "  使用: 将客户端的 baseUrl 改为 http://localhost:18791"
echo "  日志:  tail -f /tmp/cache_proxy.log"
echo "  统计:  curl -s http://localhost:18791/health | python3 -m json.tool"
echo ""
echo "🐙 感謝 感激 雨アラモード～☆"
echo ""