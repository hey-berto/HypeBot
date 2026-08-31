from datetime import UTC, datetime, timedelta

from hype_autopilot.data.collectors import MarketDataCollector
from tests.helpers import candle_series, memory_repo


class FakeClient:
    def __init__(self, missing):
        self.missing = missing

    def candles(self, symbol, interval, start, end, observation_class):
        return [item.model_copy(update={"observation_class": observation_class})
                for item in self.missing if item.symbol == symbol and item.interval == interval
                and item.open_time >= start and item.close_time <= end]


def test_gap_is_detected_and_recovered_without_rewriting_rows():
    repo = memory_repo()
    end = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    complete = candle_series("HYPE", "1m", end, 5, timedelta(minutes=1))
    missing = complete[2]
    repo.save_candles(complete[:2] + complete[3:])
    collector = MarketDataCollector(repo, FakeClient([missing]))
    assert collector.detect_gaps(end) == 1
    result = collector.recover_gaps(end)
    assert sum(result.values()) == 1
    assert repo.db.execute("SELECT status FROM collection_gaps").fetchone()[0] == "RECOVERED"
    assert len(repo.candles("HYPE", "1m", end)) == 5


def test_identical_refetch_does_not_create_raw_revisions():
    repo = memory_repo()
    end = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    rows = candle_series("HYPE", "1m", end, 5, timedelta(minutes=1))
    assert repo.save_candles(rows) == 5
    refetched = [item.model_copy(update={"received_at": item.received_at + timedelta(minutes=1)}) for item in rows]
    assert repo.save_candles(refetched) == 0
