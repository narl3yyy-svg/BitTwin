"""High level compare / sync entry points used by the GUI and the tests."""

from __future__ import annotations

from typing import Callable, Optional

from . import baseline
from .executor import Executor
from .log import RunLog
from .model import Options, PlanItem, Summary
from .planner import Planner, baseline_from_plan


def compare(lfs, rfs, opts: Options, cancel: Optional[Callable[[], bool]] = None,
            status: Optional[Callable[[str], None]] = None,
            hash_progress: Optional[Callable[[str, int], None]] = None) -> list[PlanItem]:
    say = status or (lambda m: None)
    say("Scanning left side…")
    left = lfs.scan(opts.excludes, cancel, lambda d: say(f"Scanning left: {d or '/'}"))
    say("Scanning right side…")
    right = rfs.scan(opts.excludes, cancel, lambda d: say(f"Scanning right: {d or '/'}"))
    say("Comparing…")
    base = baseline.load(lfs.uri, rfs.uri)
    planner = Planner(opts, lfs, rfs, cancel, hash_progress)
    return planner.build(left, right, base)


def synchronize(lfs, rfs, opts: Options, items: list[PlanItem],
                cancel: Optional[Callable[[], bool]] = None,
                progress: Optional[Callable[[dict], None]] = None) -> Summary:
    log = RunLog(f"sync mode={opts.mode.value} direction={opts.direction.value}")
    log.line("LEFT ", lfs.display)
    log.line("RIGHT", rfs.display)
    log.line("OPTIONS", f"compare={opts.compare.value} archive={opts.archive} "
             f"reverify_source={opts.reverify_source} tolerance={opts.time_tolerance}s")
    try:
        ex = Executor(lfs, rfs, opts, log, cancel, progress)
        summary = ex.run(items)
        old = baseline.load(lfs.uri, rfs.uri)
        baseline.save(lfs.uri, rfs.uri, baseline_from_plan(items, old))
        log.line("SUMMARY", f"copied={summary.copied} verified={summary.verified} "
                 f"bytes={summary.copied_bytes} deleted={summary.deleted} "
                 f"folders={summary.dirs_created} skipped={summary.skipped} "
                 f"failed={summary.failed} cancelled={summary.cancelled}")
    finally:
        log.close()
    summary.log_path = log.path
    return summary
