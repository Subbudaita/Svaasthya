#!/bin/sh
# Docker entrypoint hook: create the runtime roles on first database boot.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v api_password="'${SWASTHYA_API_DB_PASSWORD}'" \
  -f /swasthya/create_roles.sql
