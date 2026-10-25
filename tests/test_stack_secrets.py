import importlib.util
from pathlib import Path
import stat

import pytest

spec = importlib.util.spec_from_file_location("prepare_stack", Path(__file__).parents[1] / "scripts/prepare_stack.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_secrets_are_private_and_preserved_on_restart(tmp_path, capsys):
    module.prepare(tmp_path)
    directory = tmp_path / "runtime/secrets"
    original = {path.name: path.read_bytes() for path in directory.iterdir()}
    module.prepare(tmp_path)
    assert original == {path.name: path.read_bytes() for path in directory.iterdir()}
    assert set(original) == {"postgres_password", "paperclip_auth", "paperclip_actions",
                             "gateway_admin", "executor_agent", "paperclip_api_token"}
    assert len(set(original.values())) == len(original)
    assert original["paperclip_api_token"] == b""
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in directory.iterdir())
    assert not capsys.readouterr().out


@pytest.mark.parametrize("kind", ["symlink", "public", "directory"])
def test_unsafe_existing_secret_is_refused_without_replacement(tmp_path, kind):
    directory = tmp_path / "runtime/secrets"
    directory.mkdir(parents=True)
    target = directory / "postgres_password"
    outside = tmp_path / "public-canary"
    outside.write_text("preserve")
    if kind == "symlink":
        target.symlink_to(outside)
    elif kind == "directory":
        target.mkdir()
    else:
        target.write_text("preserve")
        target.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        module.prepare(tmp_path)
    assert outside.read_text() == "preserve"
    assert not (directory / "paperclip_auth").exists()


@pytest.mark.parametrize("name", ["runtime", "runtime/secrets"])
def test_linked_secret_directory_is_refused(tmp_path, name):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        module.prepare(tmp_path)
    assert not list(outside.iterdir())
