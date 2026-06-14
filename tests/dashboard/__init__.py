from pathlib import Path

# Mirror the other test packages: extend this package's path to the matching src/
# package so `from dashboard...` resolves under `unittest discover` (which puts tests/
# on sys.path and would otherwise shadow src/dashboard with this directory).
__path__.append(str(Path(__file__).resolve().parents[2] / "src" / "dashboard"))
