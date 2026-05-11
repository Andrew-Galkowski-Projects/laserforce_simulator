#!/bin/bash
set -e
python laserforce_simulator/manage.py migrate --noinput
exec "$@"
