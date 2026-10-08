# 雙北路邊停車智慧導航 - NAS / 邊緣運算專用輕量 Docker 映象檔
# 基礎映象檔使用極簡 Alpine Python 3.11（體積小於 50MB）
FROM python:3.11-alpine

# 安裝 git, curl, openssh（供背景定時推送 GitHub）
RUN apk add --no-cache git curl openssh-client tzdata

# 設定臺灣時區 UTC+8
ENV TZ=Asia/Taipei
RUN cp /usr/share/zoneinfo/Asia/Taipei /etc/localtime && echo "Asia/Taipei" > /etc/timezone

WORKDIR /app

# 複製專案代碼
COPY . /app

# 賦予執行權限
RUN chmod +x /app/build_live_data.py

# 啟動腳本：以無窮迴圈每 5 分鐘（300秒）準時執行一次，並支援自動 git push
CMD ["sh", "-c", "while true; do echo \"[$(date)] 啟動定時在席同步作業...\"; python3 build_live_data.py; if [ -d .git ]; then git config user.name 'nas-sync-bot'; git config user.email 'nas-sync-bot@local'; git add api_data/live_roads.json history/; git diff --staged --quiet || (git commit -m 'chore: NAS 自動定時同步雙北路邊停車在席數據 [skip ci]' && git pull --rebase origin main && git push); fi; echo \"[$(date)] 同步完成，休眠 300 秒...\"; sleep 300; done"]
