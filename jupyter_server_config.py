import os
from pathlib import Path

c = get_config()
c.ServerApp.ip = "0.0.0.0"
c.ServerApp.port = 8888
c.ServerApp.port_retries = 0
c.ServerApp.open_browser = False
c.ServerApp.root_dir = os.environ.get("LAB_WORKSPACE", "/home/jovyan/work")
c.ServerApp.allow_remote_access = True
c.IdentityProvider.token = Path(os.environ["LAB_TOKEN_PATH"]).read_text().strip()
c.ServerApp.terminals_enabled = True
