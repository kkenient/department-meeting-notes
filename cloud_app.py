"""Cloud entrypoint: require sign-in regardless of a local configuration default."""

import os
from pathlib import Path
import runpy

os.environ["AUTH_REQUIRED"] = "true"
os.environ["CLOUD_MVP"] = "true"
runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
