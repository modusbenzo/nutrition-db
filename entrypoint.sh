#!/bin/bash
set -e

echo "Waiting for database ..."
while ! python -c "
import socket, os, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect((os.environ.get('POSTGRES_HOST','db'), int(os.environ.get('POSTGRES_PORT','5432'))))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; do
    sleep 1
done
echo "Database is ready."

echo "Running migrations ..."
python manage.py migrate --noinput

echo "Collecting static files ..."
python manage.py collectstatic --noinput 2>/dev/null || true

# Optional: create superuser from env vars
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    python manage.py createsuperuser --noinput 2>/dev/null || true
    echo "Superuser checked/created."
fi

echo "Starting server ..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 120
