#!/usr/bin/env bash
set -euo pipefail
image="${1:-lab:test}"
project="lab-smoke-${RANDOM}"
export LAB_IMAGE="$image" LAB_PORT=0 LAB_BIND=127.0.0.1
export LAB_CPU_LIMIT=2.0 LAB_MEMORY_LIMIT=4g LAB_PIDS_LIMIT=1024
cleanup() {
  docker compose -p "$project" down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker compose -p "$project" up -d --pull never
container="$(docker compose -p "$project" ps -q lab)"
docker inspect "$container" --format '{{json .HostConfig}}' | python -c '
import json, sys
c = json.load(sys.stdin)
assert c["Memory"] == 4 * 1024**3
assert c["MemorySwap"] == c["Memory"]
assert c["NanoCpus"] == 2 * 10**9
assert c["PidsLimit"] == 1024
assert "/tmp/lab-spill" in c["Tmpfs"]
'
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
from labkit import run_analysis
def oversized():
    import time
    data = bytearray(256 * 1024**2)
    time.sleep(5)
    return len(data)
try:
    run_analysis(oversized, ram_gib=.125, timeout_s=20)
    raise AssertionError('Oversized managed analysis was not stopped')
except MemoryError:
    pass
assert run_analysis(lambda: 6 * 7, ram_gib=.25) == 42
assert urllib.request.urlopen(request).status == 200
with Path('lab_settings.py').open('a') as file:
    file.write('\n# persistence smoke marker\n')
PY
docker restart "$container" >/dev/null
docker exec "$container" python -c "from pathlib import Path; assert 'persistence smoke marker' in Path('lab_settings.py').read_text()"
echo 'Container smoke passed: health, non-root, authentication, CPU/RAM/swap/PID limits, bounded spill, managed OOM recovery, persistent settings.'
