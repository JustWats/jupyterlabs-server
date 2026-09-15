"""Seed persistent editable files and start the single-user Jupyter server."""
import json
import os
import secrets
import shutil
from pathlib import Path
from .hardware import assess


def seed(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for source_file in source.iterdir():
        target = destination / source_file.name
        if source_file.is_file():
            try:
                with target.open("x") as output:
                    output.write(source_file.read_text())
            except FileExistsError:
                pass  # An update must never overwrite user edits.


def main():
    os.umask(0o077)
    workspace = Path(os.environ.get("LAB_WORKSPACE", "/home/jovyan/work"))
    seed(Path(os.environ.get("LAB_STARTER", "/opt/lab-starter")), workspace)
    private = Path.home() / ".local/share/labkit"
    private.mkdir(parents=True, exist_ok=True)
    token_path = private / "token"
    supplied_file = os.environ.get("JUPYTER_TOKEN_FILE")
    token = Path(supplied_file).read_text().strip() if supplied_file else os.environ.get("JUPYTER_TOKEN")
    if token is None:
        token = token_path.read_text().strip() if token_path.exists() else secrets.token_urlsafe(32)
    if not token or len(token) < 16:
        raise ValueError("Jupyter authentication token must contain at least 16 characters")
    token_path.write_text(token + "\n")
    token_path.chmod(0o600)
    # Server config reads a private token file; do not put the token on argv.
    os.environ["LAB_TOKEN_PATH"] = str(token_path)
    inventory = assess(workspace)
    (workspace / "hardware_inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    print(f"Hardware: {inventory['cpu']['effective_cores']:g} effective CPU cores, "
          f"{inventory['memory']['effective_total_bytes'] / 1024**3:.1f} GiB RAM, "
          f"NVIDIA status={inventory['gpu']['status']}. Open 00-Setup.ipynb.", flush=True)
    os.execvp("jupyter", ["jupyter", "lab", "--config=/opt/lab-config/jupyter_server_config.py"])


if __name__ == "__main__":
    main()
