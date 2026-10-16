#!/bin/sh

CRON_SCHEDULE=${CRON_SCHEDULE:-"*/15 * * * *"}
PYTHON=/usr/local/bin/python3

echo "Installing cron job with schedule: $CRON_SCHEDULE"
echo "$CRON_SCHEDULE cd /app && $PYTHON walita.py --no-open -o /app/srv/index.html >> /proc/1/fd/1 2>&1" > /etc/cron.d/dashboard

chmod 0644 /etc/cron.d/dashboard
crontab /etc/cron.d/dashboard

# Initial run
cd /app
$PYTHON walita.py --no-open -o /app/srv/index.html

# Start cron
service cron start

# Start webserver
exec $PYTHON -m http.server 8080 --directory /app/srv
