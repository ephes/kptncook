import runpy
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_python.py"


@pytest.mark.parametrize("failure_at", [0, 1, None])
def test_audit_propagates_export_and_auditor_errors(monkeypatch, failure_at):
    calls = []

    def run(command, *, check, **kwargs):
        assert check is True
        calls.append(command)
        if len(calls) - 1 == failure_at:
            raise subprocess.CalledProcessError(23, command)

    monkeypatch.setattr(subprocess, "run", run)
    main = runpy.run_path(str(SCRIPT))["main"]
    if failure_at is None:
        main()
    else:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            main()
        assert exc.value.returncode == 23
    assert len(calls) == (1 if failure_at == 0 else 2)
    assert calls[0][1:3] == ["export", "--frozen"]
    assert "--all-groups" in calls[0]
    assert not Path(calls[0][-1]).exists()  # private scratch data is cleaned up
    if len(calls) == 2:
        assert "pip-audit==2.10.1" in calls[1]
        assert calls[1][-1] == calls[0][-1]
