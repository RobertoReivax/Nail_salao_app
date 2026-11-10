#!/bin/bash
DATE=$(date +%Y-%m-%d_%H-%M-%S)
docker exec -t nail_salon_db pg_dump -U nailuser salonschema > ./backups/backup_$DATE.sql
echo "Backup gerado com sucesso em ./backups/backup_$DATE.sql"