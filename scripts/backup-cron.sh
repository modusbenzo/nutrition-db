#!/bin/bash
# Daily database backup via Docker Compose
# Install: crontab -e → 0 3 * * * /root/nutrition-db/scripts/backup-cron.sh >> /var/log/nutrition-backup.log 2>&1

set -e

cd /root/nutrition-db

echo "[$(date)] Starting backup..."
docker compose -f docker-compose.prod.yml run --rm backup
echo "[$(date)] Backup complete."

# Count existing backups
BACKUP_COUNT=$(ls -1 /home/deploy/backups/*.sql.gz 2>/dev/null | wc -l)
echo "[$(date)] Total backups: $BACKUP_COUNT"
