"""Interface contract tests. Every provider must pass all of them."""

from collections.abc import Callable

import httpx
import pytest

from lantern_decisions.cache import InMemoryDecisionCache
from lantern_decisions.jev import JevConfig, JevProvider
from lantern_decisions.provider import DecisionProvider
from lantern_decisions.question_sets import TargetType, load_bundle
from lantern_decisions.ratelimit import TokenBucket
from lantern_decisions.stub import StubProvider
from lantern_decisions.testing import FakeJev, make_request
from lantern_decisions.types import DecisionRequest


def _stub() -> DecisionProvider:
    return StubProvider()


def _jev() -> DecisionProvider:
    return JevProvider(
        JevConfig(api_key="test-key", base_url="https://jev.test", batch_size=7),
        cache=InMemoryDecisionCache(),
        transport=httpx.MockTransport(FakeJev()),
        rate_limiter=TokenBucket(1e9, burst=10**9),
    )


PROVIDERS: dict[str, Callable[[], DecisionProvider]] = {"stub": _stub, "jev": _jev}


def _requests(n: int) -> list[DecisionRequest]:
    targets = list(TargetType)
    return [
        make_request(targets[i % 4], target_id=f"t{i}", field=f"field_{i}", lexicon_hints=[])
        for i in range(n)
    ]


@pytest.fixture(params=sorted(PROVIDERS))
def provider(request: pytest.FixtureRequest) -> DecisionProvider:
    return PROVIDERS[request.param]()


def test_answers_every_question_in_order(provider: DecisionProvider) -> None:
    requests = _requests(9)
    results = provider.decide(requests)
    bundle = load_bundle("v1")
    expected = [
        (r.target_id, q.id) for r in requests for q in bundle.for_target(r.target_type).questions
    ]
    assert [(r.target_id, r.question_id) for r in results] == expected


def test_distributions_are_complete_and_normalized(provider: DecisionProvider) -> None:
    bundle = load_bundle("v1")
    for result in provider.decide(_requests(8)):
        question = bundle.for_target(result.target_type).question(result.question_id)
        assert set(result.distribution) == set(question.option_ids)
        assert abs(sum(result.distribution.values()) - 1.0) < 1e-6
        assert result.distribution[result.answer] == result.probability
        assert result.provider == provider.name
        assert result.provider_version
        assert len(result.state_hash) == 64


def test_same_input_same_output(provider: DecisionProvider) -> None:
    requests = _requests(12)
    first = [r.to_dict() | {"cached": None} for r in provider.decide(requests)]
    second = [r.to_dict() | {"cached": None} for r in provider.decide(requests)]
    assert first == second


def test_large_batches_are_chunked(provider: DecisionProvider) -> None:
    requests = _requests(1500)
    results = provider.decide(requests)
    assert len(results) == sum(
        len(load_bundle("v1").for_target(r.target_type).questions) for r in requests
    )


def test_question_set_must_match_target(provider: DecisionProvider) -> None:
    bad = DecisionRequest(
        target_id="x",
        target_type=TargetType.SINK,
        state=make_request(TargetType.SINK).state,
        question_set_id="source",
        question_set_version="v1",
    )
    with pytest.raises(ValueError, match="applies to"):
        provider.decide([bad])


def test_unknown_question_set_version_is_rejected(provider: DecisionProvider) -> None:
    bad = DecisionRequest(
        target_id="x",
        target_type=TargetType.SOURCE,
        state=make_request(TargetType.SOURCE).state,
        question_set_id="source",
        question_set_version="v999",
    )
    with pytest.raises(ValueError):
        provider.decide([bad])
