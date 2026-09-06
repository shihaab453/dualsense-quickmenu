"""A local build must not claim a lock it did not build against."""
from importlib import metadata
import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('installed', ['0.9', None, '1.0'])
def test_build_checks_installed_versions_before_verification(monkeypatch, tmp_path, installed):
    from tools import build

    lock = tmp_path / 'requirements-dev.lock.txt'
    lock.write_text('review-package==1.0 \\\n    --hash=sha256:abc\n', encoding='utf-8')
    monkeypatch.setattr(build, '_LOCK_FILE', str(lock))
    monkeypatch.setattr(build, '_DIST_DIR', str(tmp_path / 'dist'))

    def version(name):
        assert name == 'review-package'
        if installed is None:
            raise metadata.PackageNotFoundError(name)
        return installed

    monkeypatch.setattr(metadata, 'version', version)
    verified = []
    monkeypatch.setattr(build, '_run_source_verification', lambda python: verified.append(python) or 23)
    result = build.main()
    if installed == '1.0':
        assert verified and result == 23
    else:
        assert not verified, 'The build accepted an environment that disagrees with its lock'
        assert result != 0
