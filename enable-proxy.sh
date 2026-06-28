#!/bin/bash
# 🐙 开启缓存代理
# 用法: bash ~/Desktop/cache-pool/enable-proxy.sh

CACHE_DIR="$(dirname "$0")"
LOG_FILE="/tmp/cache_proxy.log"

# Check if already running
if curl -s http://localhost:18791/health > /dev/null 2>&1; then
    echo "✅ 缓存代理已在运行"
    curl -s http://localhost:18791/health | python3 -c "
import json,sys; d=json.load(sys.stdin)['cache_stats']
print(f'   总条目: {d[\"total_entries\"]}, 总命中: {d[\"total_hits\"]}')
print(f'   DB: {d[\"db_path\"]}')
"
    exit 0
fi

echo "🚀 启动缓存代理..."
cd "$CACHE_DIR"
nohup python3 cache_proxy.py > "$LOG_FILE" 2>&1 &
sleep 3

if curl -s http://localhost:18791/health > /dev/null 2>&1; then
    echo "✅ 缓存代理启动成功 (PID: $!)"
    echo "   http://localhost:18791"
    echo "   日志: $LOG_FILE"
else
    echo "❌ 启动失败，查看日志:"
    tail -5 "$LOG_FILE"
    exit 1
fi
