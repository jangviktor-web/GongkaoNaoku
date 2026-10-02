#!/bin/bash
# 心跳保活：定期访问本地服务与线上链接，持续产生活动，阻止沙箱因空闲休眠导致应用失效。
APP="https://a2de5e308362285fb.app.workbuddy.host"
LOCAL="http://127.0.0.1:8300"
LOG="/workspace/kaogong-webapp/keepalive.log"
INTERVAL=180   # 每 3 分钟一次，足以重置常见空闲阈值

while true; do
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  code_local=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 "$LOCAL/" 2>/dev/null)
  code_app=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 15 "$APP/" 2>/dev/null)
  echo "$ts local=$code_local app=$code_app" >> "$LOG"
  sleep "$INTERVAL"
done
