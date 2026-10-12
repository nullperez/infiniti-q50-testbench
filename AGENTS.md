# Notes for AI agents

## Driving the bench
Use the MCP server `q50-bench` (see README → AI agents). Call `help`, then `status`.
Typical: `list_adapters` → `start_buses(main, av)` → `bench_set('BPLUS', true)`, `bench_set('IG', true)` →
`set_signal(...)` → `get_dashboard` / `log_tail` → `av_start` → `av_wait_ready` → `av_set_page(...)`.

Rules (from the owner's handoff, enforced in code where possible):
1. Never run bench (TX) mode on a real vehicle; there use `start_buses(..., monitor=true)`.
2. Never transmit meter-originated IDs (the bus manager refuses them).
3. Report AV success only from the meter's response (`result: confirmed`); timeouts/rejections are failures.
4. Missing/stale data is not zero (`stale: true`, `value: null`).
5. Keep simulated speed at 0 unless asked (it moves the odometer).
6. Factory reset needs the user's explicit confirmation (`av_factory_reset(confirm=true)`).

## Changing the code
* Layers: can → core → capture/signals → sim/av/hw → control → ui. Lower layers never import higher ones.
* Signals: `q50tb/signals/q50_main.py`. Keep confidence words; don't upgrade candidates without evidence.
* AV protocol knowledge and evidence: `docs/AV_PROTOCOL.md`; editable codes in `registry/`.
* Every transmit goes through `BusChannel.send` (monitor gate, protected IDs, ownership) — don't bypass it.
* Run `python -m pytest -q tests` after changes; `tools/screenshot_pages.py <dir>` renders every page offscreen.
