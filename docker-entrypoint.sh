#!/bin/sh
set -e

echo "Running migrations..."
python manage.py migrate --noinput

echo "Seeding lookup tables, carrier, admin user and test motifs..."
python manage.py seed_initial

exec "$@"
