import lantern_registry


def test_imports() -> None:
    assert lantern_registry.__version__ == "0.1.0"
