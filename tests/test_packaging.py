"""The built wheel must carry the ``pib`` project package (settings/urls/asgi).

fpgas.online-infra installs the wheel into /srv/www/pib/venv and then copies
pib/settings.py, pib/urls.py and pib/asgi.py out of it into /srv/www/pib/pib/
next to the Ansible-written local_settings.py -- so those files have to ship.

This builds the wheel for real rather than asserting on the pyproject config:
a config assertion cannot tell a working discovery setup from a broken one
(PEP-420 namespace discovery, for instance, happily claims data directories
such as pibdemos/nginx and then fails the build). The build takes a few
seconds and needs nothing beyond the build backend uv already resolves.
"""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIB_MODULES = {"pib/__init__.py", "pib/settings.py", "pib/urls.py", "pib/asgi.py", "pib/asgi.base.py"}
APP_PACKAGES = {"pibfpgas", "pistat", "pibdemos", "pibup", "ttsite", "fleet"}


IGNORED = shutil.ignore_patterns(".git", ".venv", ".worktrees", "build", "dist", "__pycache__", "*.egg-info",
                                 "*.sqlite3", "local_settings.py")


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory):
    if shutil.which("uv") is None:  # pragma: no cover - uv is how the suite is run
        pytest.skip("uv is not on PATH")
    tmp = tmp_path_factory.mktemp("pkg")
    # build from a clean copy: an in-tree build/lib left by an earlier build
    # would otherwise be reused and hide a broken package discovery config
    src = tmp / "src"
    shutil.copytree(ROOT, src, ignore=IGNORED)
    out = tmp / "wheel"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)],
        cwd=src, check=True, capture_output=True, text=True,
    )
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    with zipfile.ZipFile(wheels[0]) as z:
        return z.namelist()


def test_wheel_ships_the_pib_project_package(wheel_names):
    assert PIB_MODULES <= set(wheel_names)


def test_wheel_ships_every_django_app(wheel_names):
    assert APP_PACKAGES <= {name.split("/")[0] for name in wheel_names}


def test_wheel_ships_the_pi_fixtures(wheel_names):
    # the infra role seeds a fresh DB with `manage.py loaddata <name>`, which
    # resolves the fixture from the installed app -- so it has to ship
    assert "pibfpgas/fixtures/fpgas.online.json" in wheel_names
    assert "pibfpgas/fixtures/ps1.fpgas.online.json" in wheel_names


def test_wheel_excludes_tests_and_local_settings(wheel_names):
    assert [n for n in wheel_names if n.startswith("tests/")] == []
    assert [n for n in wheel_names if n.endswith("local_settings.py")] == []


def test_local_settings_is_gitignored():
    assert "pib/local_settings.py" in (ROOT / ".gitignore").read_text().split()
