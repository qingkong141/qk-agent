#!/bin/bash
# Docker Engine 20.10 / docker-compose 1.29.2. Updates only agent and ui.
set -euo pipefail
umask 077
cd /opt/agent
base_compose="${1:-/opt/aiot/docker-compose.yml}"
for file in "$base_compose" /opt/general.env /opt/aiot/general.hy.env /opt/agent/agent.env /opt/agent/compose.agent.yml /opt/agent/server-images.tar; do
  if [ ! -f "$file" ]; then printf 'Missing file: %s\n' "$file" >&2; exit 1; fi
done
command -v docker-compose >/dev/null
command -v curl >/dev/null
if [ "$(id -u)" != 0 ]; then echo 'Run this script as root.' >&2; exit 1; fi

# Compose v1 accepts one --env-file. Later definitions override earlier ones.
# Do not source these files: the platform also uses .NET keys containing colons.
env_tmp=$(mktemp /opt/agent/compose.env.XXXXXX)
trap 'rm -f "$env_tmp"' EXIT
{ cat /opt/general.env; printf '\n'; cat /opt/aiot/general.hy.env; printf '\n'; } > "$env_tmp"
mv "$env_tmp" /opt/agent/compose.env
compose=(docker-compose --env-file /opt/agent/compose.env -f "$base_compose" -f /opt/agent/compose.agent.yml)
"${compose[@]}" config --quiet

docker load --input /opt/agent/server-images.tar
if previous_image=$(docker inspect aiot-ui --format '{{.Image}}' 2>/dev/null); then
  rollback_tag="aiot-ui:before-agent-$(date +%Y%m%d-%H%M%S)"
  docker tag "$previous_image" "$rollback_tag"
  printf 'Previous frontend image retained: %s\n' "$rollback_tag"
fi
chmod 600 /opt/agent/agent.env /opt/agent/compose.env
mkdir -p /opt/agent/runtime/logs
chown -R 100:101 /opt/agent/runtime

"${compose[@]}" up -d --no-build --no-deps agent
ready=false
for ((attempt=0; attempt<60; attempt++)); do
  status=$(docker inspect aiot-agent --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')
  if [ "$status" = healthy ]; then ready=true; break; fi
  if [ "$status" = unhealthy ] || [ "$status" = exited ] || [ "$status" = dead ]; then break; fi
  sleep 2
done
if [ "$ready" != true ]; then
  echo 'Agent is not healthy; frontend has not been updated. Check: docker logs --tail 80 aiot-agent' >&2
  exit 1
fi

"${compose[@]}" up -d --no-build --no-deps ui
for ((attempt=0; attempt<15; attempt++)); do
  if curl --silent --fail --max-time 5 http://127.0.0.1:81/agent-api/health >/dev/null; then
    "${compose[@]}" ps agent ui
    echo 'Agent and frontend proxy are ready. Open http://SERVER_IP:81'
    exit 0
  fi
  sleep 2
done
echo 'Frontend proxy check failed. Check: docker logs --tail 80 aiot-ui' >&2
exit 1
