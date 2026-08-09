    Wired into train.py (per-interval token counts, validation success/error)
    and services.py (validate_code parse results, generate token counts).

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "event": self.event,
            "time": self.timestamp,
        }
        if self.step is not None:
            result["step"] = self.step
        if self.duration_seconds is not None:
            result["duration_seconds"] = self.duration_seconds
        if self.tokens is not None:
            result["tokens"] = self.tokens
        if self.attempt is not None:
            result["attempt"] = self.attempt
        if self.max_attempts is not None:
            result["max_attempts"] = self.max_attempts
        if self.error:
            result["error"] = self.error
        if self.error_kind:
            result["error_kind"] = self.error_kind
        if self.details:
            result["details"] = self.details
        return result


class MetricsCollector:
    """Append-only JSONL. Every write flushes so a crash doesn't lose records.

    Thread-safe by accident (appends are atomic on most filesystems), but
    I'll add a lock when I wire this into the training loop.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.records: list[MetricRecord] = []

    def record(self, event: MetricEvent, /, **fields: Any) -> MetricRecord:
        record = MetricRecord(event=event.name.lower(), **fields)
        self.records.append(record)
        if self.path is not None:
            self._write(record)
        return record

    def _write(self, record: MetricRecord) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            handle.flush()

    def count(self, event: MetricEvent) -> int:
        name = event.name.lower()
        return sum(1 for r in self.records if r.event == name)

    def summary(self) -> dict[str, Any]:
        if not self.records:
            return {"events": 0}
        kinds: dict[str, int] = {}
        total_tokens = 0
        errors = 0
        for r in self.records:
            kinds[r.event] = kinds.get(r.event, 0) + 1
            total_tokens += r.tokens or 0
            if r.error:
                errors += 1
        return {
            "events": len(self.records),
            "by_kind": kinds,
            "total_tokens": total_tokens,
            "errors": errors,
        }
