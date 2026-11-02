"""Run the focused fixture suite with socket access and plugin autoload disabled."""
import os
from pathlib import Path
import socket
import sys
import tempfile


_original_connect = socket.socket.connect


def deny_network(*args, **kwargs):
    raise RuntimeError("External network access is disabled for offline tests")


def local_only_connect(sock, address):
    # Playwright communicates with its local driver through loopback sockets.
    # All remote TCP connections remain blocked in offline test runs.
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
        return _original_connect(sock, address)
    return deny_network()


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "src"))
    socket.create_connection = deny_network
    socket.getaddrinfo = deny_network
    socket.socket.connect = local_only_connect
    socket.socket.connect_ex = deny_network
    socket.socket.sendto = deny_network
    import pytest
    # A unique ignored directory avoids touching previous pytest evidence.
    (root / "output").mkdir(exist_ok=True)
    evidence = tempfile.mkdtemp(prefix="baseline-tests-", dir=root / "output")
    test_paths = ["tests/test_catalog.py", "tests/test_categories.py",
                  "tests/test_prepare_catalog.py", "tests/test_osm.py"]
    for optional in ("experiments/source_evaluation", "experiments/foody",
                     "experiments/google_resolution",
                     "experiments/browser_framework/test_benchmark.py"):
        if (root / optional).exists():
            test_paths.append(optional)
    return pytest.main(test_paths + [
        "-q", "-p", "no:cacheprovider", "--basetemp", evidence,
    ])


if __name__ == "__main__":
    raise SystemExit(main())
