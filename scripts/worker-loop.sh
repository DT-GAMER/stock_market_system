#!/bin/sh
set -eu

INTERVAL_SECONDS="${SYNC_INTERVAL_SECONDS:-86400}"
STARTUP_SLEEP_SECONDS="${SYNC_STARTUP_SLEEP_SECONDS:-30}"

echo "equitykobo_worker_starting interval_seconds=${INTERVAL_SECONDS} startup_sleep_seconds=${STARTUP_SLEEP_SECONDS}"
sleep "${STARTUP_SLEEP_SECONDS}"

while true; do
  echo "equitykobo_worker_run_started at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if equitykobo-sync full-market; then
    echo "equitykobo_worker_run_completed at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  else
    echo "equitykobo_worker_run_failed at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  fi
  echo "equitykobo_worker_sleeping seconds=${INTERVAL_SECONDS}"
  sleep "${INTERVAL_SECONDS}"
done
