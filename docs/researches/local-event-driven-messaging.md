# Local event-driven messaging for a single-developer software factory

Date: 2026-10-27
Status: Research / recommendation
Scope: [Unified event-driven SDLC workflow](unified-event-driven-sdlc-workflow.md)

## Decision context

Loop's proposed factory runs locally for one developer, with low event volume.
High message throughput, broker availability, cross-host consumers, and durable
delivery/replay are not requirements. GitHub remains the source of truth for
issues and pull requests. SQLite can retain workflow state and checkpoints.

**Recommendation: Python standard-library `asyncio.Queue` + a small event
dispatcher; SQLite for factory state, not as a message broker.**

No Redis, RabbitMQ, NATS, background-queue framework, or queue database schema
is needed initially. Re-evaluate if the factory becomes multi-process/multi-host
or cannot tolerate losing pending in-memory messages at process exit.

## Package comparison

| Package | Mechanism | SQLite | Fit for the local factory | Limitation |
| --- | --- | --- | --- | --- |
| [`asyncio.Queue`](https://docs.python.org/3/library/asyncio-queue.html) (stdlib) | Async FIFO work queue | Independent state store | **Recommended**: zero dependencies, simple backpressure | A message goes to one consumer; no native broadcast or persistence |
| [Blinker](https://blinker.readthedocs.io/en/stable/) | In-process signals / pub-sub, including async receivers | Independent state store | Optional if signal-style subscription is preferred | No persistent queue or independent worker lifecycle |
| [bubus](https://github.com/browser-use/bubus) | In-process typed async event bus, nested events, handler forwarding | No native SQLite transport; optional JSONL event log | Useful if richer event dispatch becomes necessary | More behavior than this factory needs; JSONL log is not durable queue delivery |
| [localqueue](https://github.com/brunoportis/localqueue) | SQLite queue and optional durable pub/sub | Native | Future option for ACK/NACK, leases, retries, fan-out, consumer groups | New package with evolving API; local single-host database |
| [Huey](https://huey.readthedocs.io/en/latest/) | Background job queue, scheduler, retries, pipelines | Native `SqliteHuey` | Good if scheduled/background jobs need a framework | Task queue, not event-bus fan-out; queue item is removed on dequeue |
| [persist-queue](https://github.com/peter-wangxu/persist-queue) | FIFO and ACK-capable persistent queues | Native | Simple persistent queue alternative | Pub/sub routing and workflow semantics remain application code |
| [litequeue](https://github.com/litements/litequeue) | Persistent claimed-message queue | Native | Simple claim/status/idempotency API | Single work queue per database, not a pub/sub bus |
| [eventsourcing](https://github.com/pyeventsourcing/eventsourcing) | Event store, notifications, projections, event-sourced aggregates | Native | Only if reconstructing business state from historical events is required | More architectural commitment than a message dispatcher |

**Terminology:** A *task/work queue* delivers each item to one competing
consumer. *Publish/subscribe* delivers an event to **each subscribed handler**.
An *event store* records history; it is not automatically an execution queue.

## Minimal topology

```text
GitHub polling / direct user input
             |
             v
       asyncio.Queue
             |
             v
     one dispatcher
       |      |      |
       v      v      v
     triage  audit  metrics    <- all subscribers receive matching event
       |
       v
    commands -> one designated workflow handler / agent
       |
       v
    SQLite workflow state + checkpoints
```

The dispatcher is the **only consumer** of the shared queue. It invokes all
subscribers for the message type. Multiple workers calling `queue.get()`
would instead compete, so each event would reach only one worker.

## Small implementation sketch

```python
import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

@dataclass(frozen=True)
class Event:
    type: str
    work_item_id: str

Handler = Callable[[Event], Awaitable[None]]

class EventBus:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self.subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self.subscribers[event_type].append(handler)

    async def publish(self, event: Event) -> None:
        await self.queue.put(event)

    async def run(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                results = await asyncio.gather(
                    *(handler(event) for handler in self.subscribers[event.type]),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, Exception):
                        logging.error("Event handler failed: %r", result)
            finally:
                self.queue.task_done()
```

Example subscriptions:

```python
bus.subscribe("PullRequestUpdated", address_review_feedback)
bus.subscribe("PullRequestUpdated", record_metrics)
await bus.publish(Event("PullRequestUpdated", "issue-123"))
```

Both subscribers see the event. Failures are isolated and logged, but this
sketch **does not retry or persist failed deliveries**. Production code should
also define graceful shutdown, cancellation, bounded concurrency, and tests.

## Boundaries / failure semantics

1. **MUST:** Use one in-process dispatcher to fan out to multiple subscribers.
   Rationale: competing `asyncio.Queue` consumers do not broadcast.
2. **MUST:** Keep event facts (many handlers) distinct from action commands
   (one responsible handler). Rationale: prevents duplicate agent execution.
3. **MUST:** Treat the queue as ephemeral. A crash may lose queued events.
   Rationale: `asyncio.Queue` does not write to SQLite.
4. **MUST:** Resume from GitHub observations and persisted SQLite workflow
   state/checkpoints, not from an assumed replay of queued messages.
   Rationale: avoids pretending the transport is durable.
5. **MUST:** Recheck GitHub state and idempotency before externally visible
   writes (comments, tickets, commits, pushes, PRs), even if simpler.
   Rationale: polling and restarts may rediscover an already handled change.
6. **MUST NOT:** Hold SQLite transactions while agents, Git, CI or network
   operations run. Rationale: avoid database locks around long work.
7. **MAY:** Execute independent subscribers concurrently via
   `asyncio.gather(..., return_exceptions=True)`. Rationale: slow handlers
   need not fail other subscribers; ordering across handlers is not promised.
8. **MAY:** Use `asyncio.TaskGroup` for supervised background coroutines.
   Rationale: cancellation and lifetime management are explicit.

SQLite can store workflow state, an execution ledger (attempts, decisions,
effects, evidence), checkpoints (restart positions), and polling cursors.
Those records are **not** a durable message-delivery table.

## Impact on the existing plan

The [unified workflow plan](unified-event-driven-sdlc-workflow.md)
currently specifies a SQLite-backed **durable** bus with message, delivery,
lease, and retry tables. This research proposes a smaller alternative based on
the clarified *one local developer / low volume / no strong durability*
constraints. **The plan is not modified here.** If adopted, revise its bus and
delivery guarantees separately; do not imply both designs are implemented.

## Sources

- Python: [asyncio queues](https://docs.python.org/3/library/asyncio-queue.html),
  [task groups](https://docs.python.org/3/library/asyncio-task.html)
- [Blinker documentation](https://blinker.readthedocs.io/en/stable/)
- [bubus README](https://github.com/browser-use/bubus)
- [localqueue README and delivery guarantees](https://github.com/brunoportis/localqueue)
- [Huey SQLite storage / API](https://huey.readthedocs.io/en/latest/api.html)
- [persist-queue package](https://pypi.org/project/persist-queue/)
- [litequeue package](https://pypi.org/project/litequeue/)
- [eventsourcing README](https://github.com/pyeventsourcing/eventsourcing)
- [SQLite WAL host restriction](https://www.sqlite.org/wal.html) (relevant only
  if adopting an SQLite-backed queue: WAL cannot serve cross-host workers
  through a shared network filesystem)
