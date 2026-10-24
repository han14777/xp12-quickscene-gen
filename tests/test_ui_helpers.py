"""Model import into a job's models folder, and the native file dialog plumbing (dialog itself patched out)."""

import pytest

from quickscene import overrides
from quickscene.config import Job
from quickscene.gui import picker

from test_pipeline import ZONE, make_settings


@pytest.fixture
def job(tmp_path):
    return Job.create("models", ZONE, make_settings(tmp_path))


def test_import_model_copies_textures_and_rewrites_paths(job, tmp_path):
    src = tmp_path / "downloads" / "tower" / "objects"
    (src.parent / "textures").mkdir(parents=True)
    src.mkdir()
    (src.parent / "textures" / "tower.png").write_bytes(b"png")
    (src.parent / "textures" / "tower.dds").write_bytes(b"dds")
    (src / "tower.obj").write_text("I\n800\nOBJ\n\nTEXTURE ../textures/tower.png\nTEXTURE_LIT ../textures/tower_LIT.png\n"
                                   "POINT_COUNTS 0 0 0 0\n", encoding="latin-1")
    r = overrides.import_model(job, src / "tower.obj")
    assert r == {"path": "tower/tower.obj", "copied": ["tower.dds", "tower.obj", "tower.png"],
                 "missing": ["../textures/tower_LIT.png"], "converted": False, "warnings": []}
    text = (job.models_dir / "tower" / "tower.obj").read_text(encoding="latin-1")
    assert "TEXTURE tower.png\n" in text and "TEXTURE_LIT ../textures/tower_LIT.png" in text
    assert overrides.model_is_local(job, r["path"])
    # A file already inside the models folder is used where it is
    assert overrides.import_model(job, job.models_dir / "tower" / "tower.obj")["copied"] == []
    with pytest.raises(ValueError):
        overrides.import_model(job, tmp_path / "missing.obj")


def test_picker_runs_the_dialog_in_a_child_process(monkeypatch, tmp_path):
    calls = []

    class Done:
        def __init__(self, stdout, stderr=""):
            self.stdout, self.stderr = stdout, stderr

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Done('noise\n{"path": "C:/X-Plane 12"}\n')

    monkeypatch.setattr(picker.subprocess, "run", fake_run)
    assert picker.pick("folder", "Pick", "D:/x") == "C:/X-Plane 12"
    assert calls[0][-6:] == ["pick", "folder", "--title", "Pick", "--initial", "D:/x"]
    monkeypatch.setattr(picker.subprocess, "run", lambda cmd, **k: Done('{"path": null}'))
    assert picker.pick("exe") is None
    monkeypatch.setattr(picker.subprocess, "run", lambda cmd, **k: Done("", "Traceback: no display"))
    with pytest.raises(RuntimeError, match="no display"):
        picker.pick("obj")
    with pytest.raises(ValueError):
        picker.pick("anything")


def test_dialog_child_reports_choice_as_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(picker, "show_dialog", lambda kind, title, initial: str(tmp_path))
    assert picker.child_main("folder", "", "") == 0
    assert capsys.readouterr().out.strip() == '{"path": ' + __import__("json").dumps(str(tmp_path)) + "}"
    assert picker._initial_dir(str(tmp_path / "nope" / "file.obj")) is None
    assert picker._initial_dir(str(tmp_path / "file.obj")) == str(tmp_path)
