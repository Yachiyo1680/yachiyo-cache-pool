#!/bin/bash
# 🐙 ヤチヨの魔法のキャッシュプール～☆
# Yachiyo's Magic Cache Pool - Easy CLI Wrapper

cd "$(dirname "$0")"

case "${1:-help}" in
  put)
    shift
    echo "🧐 正在计算语义向量（用 bge-m3）..."
    python3 -c "
import cache_pool as cp, sys, json
query = sys.argv[1]
resp = sys.argv[2] if len(sys.argv) > 2 else input('响应内容: ')
print('  计算 embedding...')
emb = cp.get_embedding(query)
eid = cp.store(query, resp, embedding=emb)
print(f'✅ 已缓存！(ID: {eid})')
" "$@"
    ;;
  get|search)
    shift
    query="$*"
    [ -z "$query" ] && read -p "🔍 想搜什么？: " query
    python3 -c "
import cache_pool as cp, sys
query = sys.argv[1]
result = cp.search(query)
if result['hit']:
    print(f'✅ 命中！({result[\"match_type\"]})')
    if 'similarity' in result:
        print(f'   相似度: {result[\"similarity\"]}')
    print(f'   命中次数: {result[\"hit_count\"]}')
    print(f'   📝 缓存内容: {result[\"response\"][:300]}')
else:
    print('❌ 没命中缓存，发 API 吧～')
" "$query"
    ;;
  stats)
    python3 cache_pool.py stats
    ;;
  clean)
    python3 cache_pool.py vacuum
    echo "🧹 清理完毕！"
    ;;
  demo)
    echo "🎮 开始 Demo！先存几条数据看看～"
    
    echo ""
    echo "📥 第1条: 天气查询"
    python3 -c "
import cache_pool as cp
q = '今天上海天气怎么样'
r = '上海今天晴天，气温28-32°C，适合出去走走☀️'
emb = cp.get_embedding(q)
eid = cp.store(q, r, embedding=emb)
print(f'   ✅ ID:{eid}')
"
    
    echo "📥 第2条: F1"
    python3 -c "
import cache_pool as cp
q = 'F1巴塞罗那站汉密尔顿夺冠了吗'
r = '是的！汉密尔顿在加泰罗尼亚赛道拿下红牛阵营首胜，从P5一路超回P1，精彩绝伦🏎️🔥'
emb = cp.get_embedding(q)
eid = cp.store(q, r, embedding=emb)
print(f'   ✅ ID:{eid}')
"
    
    echo "📥 第3条: 博客"
    python3 -c "
import cache_pool as cp
q = 'Yachiyo的博客地址是什么'
r = '博客在 https://cks114.top/，每周六更新一篇杂谈文章📝'
emb = cp.get_embedding(q)
eid = cp.store(q, r, embedding=emb)
print(f'   ✅ ID:{eid}')
"
    
    echo ""
    echo "📊 现在试试不同姿势搜索～"
    echo ""
    
    echo "🔍 测试1: 完全一样的问题"
    python3 -c "
import cache_pool as cp
r = cp.search('F1巴塞罗那站汉密尔顿夺冠了吗')
print(f'   → {\"✅ HIT\" if r[\"hit\"] else \"❌ MISS\"} ({r.get(\"match_type\",\"\")})')
if r['hit']: print(f'   回复: {r[\"response\"][:60]}...')
"
    
    echo "🔍 测试2: 换种说法（语义匹配）"
    python3 -c "
import cache_pool as cp
r = cp.search('汉密尔顿在巴塞罗那赢了没')
print(f'   → {\"✅ HIT\" if r[\"hit\"] else \"❌ MISS\"} ({r.get(\"match_type\",\"\")}, sim={r.get(\"similarity\",\"N/A\")})')
if r['hit']: print(f'   回复: {r[\"response\"][:60]}...')
"
    
    echo "🔍 测试3: 完全不同的问题（应该 MISS）"
    python3 -c "
import cache_pool as cp
r = cp.search('今天晚上吃什么好')
print(f'   → {\"✅ HIT\" if r[\"hit\"] else \"❌ MISS\"}')
"
    
    echo ""
    echo "📊 最终统计"
    python3 -c "
import cache_pool as cp
s = cp.stats()
print(f'   总条目: {s[\"total_entries\"]}, 总命中: {s[\"total_hits\"]}')
"

    echo ""
    echo "✨ Demo 完毕！Cache Pool 正常工作！✨"
    ;;
  *)
    echo "🐙 ヤチヨのキャッシュプール～☆"
    echo ""
    echo "用法:"
    echo "  $0 put <问题> <回答>     存入缓存"
    echo "  $0 search <问题>         搜索缓存"
    echo "  $0 stats                 查看统计"
    echo "  $0 clean                 清理过期"
    echo "  $0 demo                  跑个Demo玩玩"
    echo ""
    echo "示例:"
    echo "  bash cache.sh demo"
    echo "  bash cache.sh search '今天天气怎么样'"
    echo "  bash cache.sh put '天空是什么颜色' '蓝色☀️'"
    ;;
esac
