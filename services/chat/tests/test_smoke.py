import pytest
from chat.main import main


def test_main_prints_greeting(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    assert "Hello from chat!" in capsys.readouterr().out
