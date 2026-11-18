"""Tests für den SSE-Queue-Lifecycle in utils.agent_runner."""

import asyncio

import pytest
from utils.agent_runner import (
    _SSE_BEENDET,
    hole_sse_queue,
    markiere_sse_beendet,
    sende_sse_event,
    sse_queues,
)


@pytest.fixture(autouse=True)
def _saubere_queues():
    """Setzt das globale sse_queues-Dict vor jedem Test zurück."""
    sse_queues.clear()
    yield
    sse_queues.clear()


def test_hole_sse_queue_legt_lazy_an():
    """Beim ersten Aufruf wird eine neue Queue erzeugt und gemerkt."""
    q = hole_sse_queue("job-1")
    assert isinstance(q, asyncio.Queue)
    assert sse_queues["job-1"] is q


def test_hole_sse_queue_liefert_bestehende():
    """Mehrere Aufrufe für denselben Job liefern dieselbe Queue."""
    q1 = hole_sse_queue("job-2")
    q2 = hole_sse_queue("job-2")
    assert q1 is q2


def test_markiere_sse_beendet_blockiert_neue_queue():
    """Nach markiere_sse_beendet liefert hole_sse_queue None."""
    hole_sse_queue("job-3")  # echte Queue anlegen
    markiere_sse_beendet("job-3")

    assert hole_sse_queue("job-3") is None
    assert sse_queues["job-3"] is _SSE_BEENDET


@pytest.mark.asyncio
async def test_sende_sse_event_verwirft_nach_stream_ende():
    """Events, die nach Stream-Ende gesendet werden, dürfen keine neue Queue anlegen."""
    # Stream endet, bevor der Sender überhaupt eine Queue benutzt hat.
    markiere_sse_beendet("job-4")

    await sende_sse_event("job-4", "formal", "ok", "fertig", 100)

    # Garantie: kein lazy-Re-Insert mit echter Queue (das wäre der Memory-Leak).
    assert sse_queues["job-4"] is _SSE_BEENDET


@pytest.mark.asyncio
async def test_sende_sse_event_landet_in_queue_solange_aktiv():
    """Solange der Stream aktiv ist, kommen Events normal an."""
    q = hole_sse_queue("job-5")
    await sende_sse_event("job-5", "formal", "laufend", "läuft", 50)

    event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event == {
        "agent": "formal",
        "status": "laufend",
        "message": "läuft",
        "progress": 50,
    }
