#!/usr/bin/env bash
set -euo pipefail
image="${1:-lab:test}"
container="lab-smoke-${RANDOM}"
volume="${container}-home"
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker volume create "$volume" >/dev/null
docker run -d --name "$container" --memory=4g --cpus=2 --shm-size=256m \
  -v "$volume:/home/jovyan" "$image" >/dev/null
for attempt in $(seq 1 60); do
  state="$(docker inspect --format '{{.State.Health.Status}}' "$container")"
  if [[ "$state" == healthy ]]; then break; fi
  if [[ "$attempt" == 60 ]]; then
    docker logs "$container"
    exit 1
  fi
  sleep 2
done
docker exec -i "$container" python - <<'PY'
import json, os, urllib.request, urllib.error
from pathlib import Path
from labkit import assess
assert os.getuid() == 1000
hardware = assess()
assert hardware['memory']['effective_total_bytes'] <= 4 * 1024**3
assert hardware['cpu']['effective_cores'] <= 2
assert Path('00-Setup.ipynb').exists()
base = 'http://127.0.0.1:8888'
try:
    urllib.request.urlopen(base + '/api/contents')
    raise AssertionError('Unauthenticated contents access succeeded')
except urllib.error.HTTPError as error:
    assert error.code in (401, 403)
token = (Path.home() / '.local/share/labkit/token').read_text().strip()
request = urllib.request.Request(base + '/api/contents', headers={'Authorization': 'token ' + token})
assert urllib.request.urlopen(request).status == 200
with Path('lab_settings.py').open('a') as file:
    file.write('\n# persistence smoke marker\n')
PY
docker restart "$container" >/dev/null
docker exec "$container" python -c "from pathlib import Path; assert 'persistence smoke marker' in Path('lab_settings.py').read_text()"
echo 'Container smoke passed: health, non-root, authentication, CPU/RAM limits, persistent settings.'
