#!/bin/bash
# 🐙 一键启动缓存代理 + 切换 OpenClaw
# Iroha 用这个来开启缓存省钱模式～

echo "🐙 ヤチヨのキャッシュプロキシ～☆"
echo "=============================="

# Step 1: Start the cache proxy
if curl -s http://localhost:18791/health > /dev/null 2>&1; then
    echo "✅ 缓存代理已在运行中"
else
    echo "🚀 启动缓存代理..."
    nohup python3 "$(dirname "$0")/cache_proxy.py" > /tmp/cache_proxy.log 2>&1 &
    sleep 3
    if curl -s http://localhost:18791/health > /dev/null 2>&1; then
        echo "✅ 缓存代理启动成功"
    else
        echo "❌ 代理启动失败，看日志: tail /tmp/cache_proxy.log"
        exit 1
    fi
fi

# Step 2: Check current config
echo ""
echo "📋 OpenClaw 配置状态:"
# Verify models order
python3 -c "
import json
with open('$HOME/.openclaw/openclaw.json') as f:
    c = json.load(f)
models = list(c['agents']['defaults']['models'].keys())
ds = c['models']['providers']['deepseek']['baseUrl']
ds_c = c['models']['providers'].get('deepseek-cached',{}).get('baseUrl','')
print(f'  原生 DeepSeek:  {ds}')
print(f'  缓存版 DeepSeek: {ds_c}')
print(f'  首选模型: {models[0]}')
print(f'  Fallback: {models[1]}')
"

echo ""
echo "🎯 已就绪！OpenClaw 将优先走缓存"
echo "   如果代理挂了自动退回原生 API"
echo ""
echo "🔍 查看代理日志: tail -f /tmp/cache_proxy.log"
echo "📊 查看缓存统计: curl -s http://localhost:18791/health | python3 -m json.tool"
