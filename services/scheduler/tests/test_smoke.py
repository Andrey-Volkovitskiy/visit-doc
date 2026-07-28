import pytest
from scheduler.main import main


def test_main_prints_greeting(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    assert "Hello from scheduler!" in capsys.readouterr().out
