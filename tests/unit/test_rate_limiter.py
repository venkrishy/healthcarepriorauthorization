"""Unit tests for the DynamoDB atomic rate limiter."""

import datetime
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from authagent.tools.rate_limiter import RateLimitExceeded, check_and_increment, get_current_usage


def _make_conditional_check_error():
    return ClientError(
        error_response={"Error": {"Code": "ConditionalCheckFailedException", "Message": "Limit exceeded"}},
        operation_name="UpdateItem",
    )


@pytest.fixture
def mock_table():
    with patch("authagent.tools.rate_limiter._dynamodb") as mock_dynamo:
        table = MagicMock()
        mock_dynamo.Table.return_value = table
        yield table


def test_check_and_increment_success(mock_table):
    """Happy path: both daily and monthly counters increment successfully."""
    mock_table.update_item.return_value = {"Attributes": {"count": {"N": "5"}}}
    # Patch to return Decimal-compatible value
    mock_table.update_item.return_value = {"Attributes": {"count": 5}}

    result = check_and_increment()

    assert mock_table.update_item.call_count == 2
    assert "counters" in result


def test_check_and_increment_daily_limit_exceeded(mock_table):
    """Daily limit hit raises RateLimitExceeded with daily period."""
    mock_table.update_item.side_effect = _make_conditional_check_error()

    with pytest.raises(RateLimitExceeded) as exc_info:
        check_and_increment()

    assert exc_info.value.period == "daily"
    assert exc_info.value.retry_after_seconds > 0


def test_check_and_increment_monthly_limit_exceeded(mock_table):
    """Monthly limit hit raises RateLimitExceeded with monthly period."""
    # Daily succeeds, monthly fails
    mock_table.update_item.side_effect = [
        {"Attributes": {"count": 50}},
        _make_conditional_check_error(),
    ]

    with pytest.raises(RateLimitExceeded) as exc_info:
        check_and_increment()

    assert exc_info.value.period == "monthly"


def test_get_current_usage(mock_table):
    """get_current_usage returns correct structure."""
    mock_table.get_item.return_value = {"Item": {"count": 42}}

    usage = get_current_usage()

    assert "daily" in usage
    assert "monthly" in usage
    assert usage["daily"]["count"] == 42
    assert usage["daily"]["limit"] == 100
    assert usage["daily"]["remaining"] == 58
