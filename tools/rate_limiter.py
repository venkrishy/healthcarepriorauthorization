"""
DynamoDB atomic counter rate limiter.

Two-layer defense (Layer 2) — API Gateway Usage Plans are Layer 1.
Prevents any race conditions via DynamoDB conditional updates.

Table: authagent-rate-limits
  PK: "DAILY#YYYY-MM-DD" or "MONTHLY#YYYY-MM"
  SK: "COUNTER"
  count: number (atomic increment)
  ttl: epoch seconds (auto-expire via DynamoDB TTL)
"""

import datetime
import os

import boto3
from botocore.exceptions import ClientError

RATE_LIMITS_TABLE = os.environ.get("RATE_LIMITS_TABLE", "authagent-rate-limits")
DAILY_LIMIT = int(os.environ.get("DAILY_REQUEST_LIMIT", "100"))
MONTHLY_LIMIT = int(os.environ.get("MONTHLY_REQUEST_LIMIT", "3100"))

_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


class RateLimitExceeded(Exception):
    """Raised when the daily or monthly request limit has been hit."""

    def __init__(self, period: str, limit: int, retry_after_seconds: int):
        self.period = period
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{period} rate limit of {limit} requests exceeded")


def check_and_increment() -> dict:
    """
    Atomically check and increment the daily and monthly request counters.

    Raises RateLimitExceeded if either counter has reached its cap.
    Returns the updated counter values on success.
    """
    table = _dynamodb.Table(RATE_LIMITS_TABLE)
    today = datetime.date.today().isoformat()
    month = today[:7]  # "YYYY-MM"

    # TTL: daily keys expire after 2 days, monthly after 35 days
    daily_ttl = int(
        (datetime.datetime.now() + datetime.timedelta(days=2)).timestamp()
    )
    monthly_ttl = int(
        (datetime.datetime.now() + datetime.timedelta(days=35)).timestamp()
    )

    counters = [
        (f"DAILY#{today}", DAILY_LIMIT, daily_ttl, "daily"),
        (f"MONTHLY#{month}", MONTHLY_LIMIT, monthly_ttl, "monthly"),
    ]

    incremented = []
    for pk, limit, ttl, period in counters:
        try:
            response = table.update_item(
                Key={"pk": pk, "sk": "COUNTER"},
                UpdateExpression="ADD #count :one SET #ttl = if_not_exists(#ttl, :ttl)",
                ConditionExpression="#count < :limit OR attribute_not_exists(#count)",
                ExpressionAttributeNames={"#count": "count", "#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":one": 1,
                    ":limit": limit,
                    ":ttl": ttl,
                },
                ReturnValues="UPDATED_NEW",
            )
            new_count = int(response["Attributes"]["count"])
            incremented.append({"period": period, "count": new_count, "limit": limit})
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Determine retry-after: daily resets at midnight, monthly at month end
                if period == "daily":
                    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
                    reset = datetime.datetime.combine(tomorrow, datetime.time.min)
                    retry_after = int((reset - datetime.datetime.now()).total_seconds())
                else:
                    next_month = (datetime.date.today().replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
                    reset = datetime.datetime.combine(next_month, datetime.time.min)
                    retry_after = int((reset - datetime.datetime.now()).total_seconds())
                raise RateLimitExceeded(period, limit, retry_after) from exc
            raise

    return {"counters": incremented}


def get_current_usage() -> dict:
    """Return current daily and monthly usage counts without incrementing."""
    table = _dynamodb.Table(RATE_LIMITS_TABLE)
    today = datetime.date.today().isoformat()
    month = today[:7]

    usage = {}
    for pk, period, limit in [
        (f"DAILY#{today}", "daily", DAILY_LIMIT),
        (f"MONTHLY#{month}", "monthly", MONTHLY_LIMIT),
    ]:
        response = table.get_item(Key={"pk": pk, "sk": "COUNTER"})
        count = int(response.get("Item", {}).get("count", 0))
        usage[period] = {"count": count, "limit": limit, "remaining": max(0, limit - count)}

    return usage
