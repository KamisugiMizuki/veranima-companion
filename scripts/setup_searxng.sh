#!/bin/bash
# SearXNG 部署脚本：重建容器 + 应用配置（可复现）
#
# 用法：bash scripts/setup_searxng.sh
# 结果：http://127.0.0.1:8080（JSON API: /search?q=<query>&format=json）
#
# 注意：会删除并重建名为 searxng 的容器（配置卷 searxng-config 保留内容但被覆盖）
set -e

echo "==> 停止并删除旧容器"
docker stop searxng 2>/dev/null || true
docker rm searxng 2>/dev/null || true

echo "==> 创建命名卷（配置 + 缓存）"
docker volume create searxng-config >/dev/null 2>&1 || true
docker volume create searxng-cache >/dev/null 2>&1 || true

echo "==> 启动容器（仅本机 127.0.0.1:8080）"
docker run -d --name searxng \
  -p 127.0.0.1:8080:8080 \
  -v searxng-config:/etc/searxng \
  -v searxng-cache:/var/cache/searxng \
  searxng/searxng:latest >/dev/null

echo "==> 应用配置"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker cp "$SCRIPT_DIR/../config/searxng-settings.yml" searxng:/etc/searxng/settings.yml
docker exec searxng chown searxng:searxng /etc/searxng/settings.yml
docker restart searxng >/dev/null

echo "==> 等待就绪"
sleep 8
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "http://127.0.0.1:8080/" || true)
echo "SearXNG HTTP $code — http://127.0.0.1:8080"
echo "验证搜索：curl 'http://127.0.0.1:8080/search?q=test&format=json'"
