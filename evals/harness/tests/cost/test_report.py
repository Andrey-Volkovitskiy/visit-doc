from decimal import Decimal
from pathlib import Path

import pytest
from golden_harness.cli import main
from golden_harness.cost.report import (
    UsageContractError,
    call_site,
    cost_report,
    render_cost,
)
from golden_harness.record import CaseRun, ReplyTurn, write_case
from pydantic import JsonValue

_SONNET = "claude-sonnet-5"
_HAIKU = "claude-haiku-4-5-20251001"


def _usage(
    call: str,
    *,
    turn: str = "turn-1",
    model: str = _SONNET,
    **counts: int | None,
) -> dict[str, JsonValue]:
    spent: dict[str, JsonValue] = {"input": 100, "output": 10}
    spent.update(counts)
    return {
        "event": "model.usage",
        "turn_id": turn,
        "call": call,
        "model": model,
        "stop_reason": "end_turn",
        "usage": {key: value for key, value in spent.items() if value is not None},
        "level": "info",
    }


def _case(
    run_dir: Path,
    case_id: str,
    events: list[dict[str, JsonValue]] | None,
    reply_events: list[dict[str, JsonValue]] | None = None,
) -> None:
    write_case(
        run_dir,
        CaseRun(
            case_id=case_id,
            chat_id=f"chat-{case_id}",
            attempts=1,
            elapsed_seconds=1.0,
            events=events,
            reply_turn=(
                ReplyTurn(events=reply_events) if reply_events is not None else None
            ),
        ),
    )


def test_a_booking_loops_iterations_are_one_call_site_counted_per_call_and_turn(
    tmp_path: Path,
) -> None:
    _case(
        tmp_path,
        "G-a-01",
        [
            {"event": "intent.classified", "turn_id": "turn-1"},
            _usage("handle_booking.model[1]", cache_read=0, cache_write=0),
            _usage("handle_booking.model[2]", cache_read=0, cache_write=0),
        ],
        reply_events=[
            _usage(
                "handle_booking.model[1]", turn="turn-2", cache_read=0, cache_write=0
            )
        ],
    )

    (site,) = cost_report(tmp_path).sites

    assert (site.call_site, site.calls, len(site.turns)) == (
        "handle_booking.model",
        3,
        2,
    )
    assert site.input == 300


def test_a_call_is_priced_at_its_models_list_price_by_cache_lifetime(
    tmp_path: Path,
) -> None:
    _case(
        tmp_path,
        "G-a-01",
        [
            _usage(
                "handle_booking.model[1]",
                input=1000,
                output=500,
                cache_read=2000,
                cache_write=400,
                cache_write_1h=100,
            )
        ],
    )

    report = cost_report(tmp_path)

    # 1000 x $2 + 500 x $10 + 2000 x $0.20 + 300 x $2.50 + 100 x $4, per million.
    assert report.cost == Decimal("0.00855")


def test_hit_rates_count_calls_reading_the_cache_and_the_input_it_served(
    tmp_path: Path,
) -> None:
    _case(
        tmp_path,
        "G-a-01",
        [
            _usage("handle_booking.model[1]", input=100, cache_read=0, cache_write=300),
            _usage("handle_booking.model[2]", input=100, cache_read=300, cache_write=0),
        ],
    )

    (site,) = cost_report(tmp_path).sites

    assert site.call_hit_rate == 0.5
    assert site.token_hit_rate == 300 / 800


def test_thinking_is_a_share_of_the_output_of_the_calls_that_reported_it(
    tmp_path: Path,
) -> None:
    _case(
        tmp_path,
        "G-g-01",
        [
            _usage(
                "small_talk.model", output=100, thinking=40, cache_read=0, cache_write=0
            ),
            _usage("small_talk.model", output=900, cache_read=0, cache_write=0),
        ],
    )

    (site,) = cost_report(tmp_path).sites

    assert (site.thinking, site.thinking_share) == (40, 0.4)


def test_a_model_with_no_price_leaves_its_site_and_the_total_unpriced(
    tmp_path: Path,
) -> None:
    _case(
        tmp_path,
        "G-a-01",
        [
            _usage("classify_intent.model", model=_HAIKU, cache_read=0, cache_write=0),
            _usage(
                "compose_answer.model", model="some-model", cache_read=0, cache_write=0
            ),
        ],
    )

    report = cost_report(tmp_path)

    by_site = {site.call_site: site for site in report.sites}
    assert by_site["classify_intent.model"].cost is not None
    assert by_site["compose_answer.model"].cost is None
    assert by_site["compose_answer.model"].unpriced_calls == 1
    assert report.cost is None
    assert "unpriced (1 calls)" in render_cost(report)


def test_a_cache_write_of_no_stated_lifetime_is_unpriced(tmp_path: Path) -> None:
    _case(
        tmp_path,
        "G-a-01",
        [_usage("handle_booking.model[1]", cache_read=0, cache_write=500)],
    )

    assert cost_report(tmp_path).cost is None


def test_a_call_missing_its_cache_counts_has_no_hit_rate_and_no_price(
    tmp_path: Path,
) -> None:
    _case(tmp_path, "G-a-01", [_usage("small_talk.model")])

    report = cost_report(tmp_path)

    (site,) = report.sites
    assert (site.call_hit_rate, site.token_hit_rate, report.cost) == (None, None, None)


def test_a_case_that_logged_no_model_call_is_counted_but_spends_nothing(
    tmp_path: Path,
) -> None:
    _case(tmp_path, "G-a-01", [_usage("small_talk.model", cache_read=0, cache_write=0)])
    _case(tmp_path, "G-b-01", None)

    report = cost_report(tmp_path)

    assert (report.cases, report.cases_with_usage) == (2, 1)


def test_a_run_with_no_usage_entries_says_so_rather_than_reporting_zero(
    tmp_path: Path,
) -> None:
    _case(tmp_path, "G-a-01", [{"event": "intent.classified", "turn_id": "turn-1"}])

    rendered = render_cost(cost_report(tmp_path))

    assert "No model call was logged" in rendered
    assert "$" not in rendered


def test_an_entry_the_contract_cannot_describe_is_refused_naming_its_case(
    tmp_path: Path,
) -> None:
    broken = _usage("small_talk.model")
    broken["usage"] = {"input": "100", "output": 10}
    _case(tmp_path, "G-g-02", [broken])

    with pytest.raises(UsageContractError, match="G-g-02"):
        cost_report(tmp_path)


@pytest.mark.parametrize(
    ("call", "site"),
    [
        ("handle_booking.model[12]", "handle_booking.model"),
        ("answer_faq.model", "answer_faq.model"),
    ],
)
def test_a_call_site_drops_only_an_iteration_suffix(call: str, site: str) -> None:
    assert call_site(call) == site


def test_the_cost_command_prints_the_report_for_a_run_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "01RUN"
    _case(
        run_dir,
        "G-a-01",
        [_usage("small_talk.model", input=1000, output=0, cache_read=0, cache_write=0)],
    )

    status = main(["cost", "--run", str(run_dir)])

    printed = capsys.readouterr().out
    assert status == 0
    assert "# Model spend - run 01RUN" in printed
    assert "| small_talk.model | claude-sonnet-5 | 1 | 1 |" in printed
    assert "$0.0020" in printed
