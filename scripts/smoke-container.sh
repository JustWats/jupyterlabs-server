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
import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
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
# Fetch the actual frontend, then every script and stylesheet it references.
# A login page or JSON API response alone does not validate the Lab interface.
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.urls.append(attrs['src'])
        if tag == 'link' and 'stylesheet' in attrs.get('rel', '').split():
            self.urls.append(attrs['href'])
def authenticated_get(url):
    req = urllib.request.Request(url, headers={'Authorization': 'token ' + token})
    return urllib.request.urlopen(req, timeout=30)
with authenticated_get(base + '/lab') as response:
    assert response.status == 200
    assert urlsplit(response.url).path == '/lab', response.url
    html = response.read().decode()
assert 'jupyter-config-data' in html
assets = Assets()
assets.feed(html)
assert any('.js' in urlsplit(url).path for url in assets.urls), 'No frontend JavaScript found'
for asset in assets.urls:
    url = urljoin(base + '/lab', asset)
    assert urlsplit(url).netloc == urlsplit(base).netloc, url
    with authenticated_get(url) as response:
        assert response.status == 200, url
        assert 'text/html' not in response.headers.get('Content-Type', ''), url
        assert response.read(), url
print(f'JupyterLab HTML and {len(assets.urls)} frontend assets passed')
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
