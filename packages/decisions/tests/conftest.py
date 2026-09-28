from __future__ import annotations

from collections.abc import Callable

import pytest

from lantern_decisions.testing import FakeJev


@pytest.fixture()
def fake_jev() -> FakeJev:
    return FakeJev()


@pytest.fixture()
def no_sleep() -> tuple[list[float], Callable[[float], None]]:
    slept: list[float] = []
    return slept, slept.append
