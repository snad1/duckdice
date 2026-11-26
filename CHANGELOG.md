# Changelog

## CLI v1.15.0 / TG v1.11.0 / duckctl v3.5 - API key vault, per-lane account (2026-11-26)

Ports shuffle 1.15.0's key vault and per-lane account (the same code as goated 1.16.0). Needs casino-shared 0.11.0; with an older one the CLI prints `casino-shared 0.11.0 or newer is needed: run duckctl update` and exits 1.

### Key vault
- The API key lives in the host vault (`~/.casino-vault/duckdice/`, 0600, encrypted) as `default`. On lane 1's start a key in the config file is moved there once it reads back equal; `api_key` leaves the config and one copy stays at `~/.duckdice_autobot.json.pre-vault` (0600).
- `DUCKDICE_API_KEY` from the environment is moved into the vault the same way. Once the vault holds the key it wins, and each lane 1 start says `DUCKDICE_API_KEY is still set: the vault holds the key now, remove it from ~/duckdice-bot/.env`. An env key is never written to the config file, even when the vault can't be used.
- `duckctl key list | add ID [--label L] [--kind key|proxy|telegram] | rotate ID | delete ID`: the secret is read through a hidden prompt and reaches the bot on stdin only, never in argv or a log. `list` shows id, kind, label, last 4 and the lanes using each entry; a key in use can't be deleted; the Telegram token is only `telegram`.
- `--config-set` refuses `api_key` with or without a `laneK.` prefix.

### Per-lane account
- A lane may bet from its own key (`laneK.key_ref=ID`), currency (`laneK.currency=`) and proxy (`laneK.proxy=` or a vault proxy with `laneK.proxy_ref=ID`). A proxy with a password is refused as a plain value: it goes in the vault.
- A key or vault proxy that isn't in the vault is refused (`Invalid lane3 override: no key named nobody in the vault`), nothing is saved; a lane whose key is gone exits at start instead of betting from another account. A lane on its own key never falls back to the env key.
- A config-set that changes a running lane's key, currency or vault proxy restarts only that lane: `Lane 3 restarted: its key, currency or proxy changed.`
- Splits (budget share, risky-config check, `basebet_pct`) count only the enabled lanes on the same key and currency. The faucet stays shared and can't be set per lane.

### Telegram
- The Telegram bot reads its token from the vault (`duckctl key add telegram --kind telegram`), then `DUCKDICE_TG_TOKEN`.

### Tests
- `tests/test_lane_accounts.py` (migration and backup, broken vault, lanes 2+ never write the vault, own key / currency / proxy, account split, missing key or proxy, vault proxy never written, refusals without echo, restart of the changed lane only, key verbs, Telegram token, env key moved and never written, vault beats env, own key ignores env, a lane key masked in logs).
- `tests/test_duckctl_lanes.py` (`key add` and `rotate` send the secret on stdin only, bad `key` arguments exit 2).
- `tests/conftest.py` points the vault at each test's tmp dir; e5, e5_lanes, e7, e9, portability and registry tests updated.

## CLI v1.14.0 / duckctl v3.4 - named lanes, one lane at a time, pause / resume / reload (2026-11-25)

Ports WolfBet's lane registry and one-lane verbs (WolfBet v2.48.0 to v2.49.0, wolfctl 3.22 to 3.24), the same code as goated 1.15.0, shuffle 1.14.0 and stake 1.17.0, plus wolfctl's `pause`, `resume` and `reload`. Needs casino-shared 0.10.0; with an older one the CLI prints `casino-shared 0.10.0 or newer is needed: run duckctl update` and exits 1. Every lane still shares one account.

### Lanes
- Lanes have an id and a name: `lanes: {split, max, next_id, items: {"<id>": {name, enabled, overrides}}}`. A config with `lanes.count` reads as lanes 1..count, as before; the first lane write keeps one copy at `~/.duckdice_autobot.json.pre-registry` (0600).
- `duckctl lane add NAME [--from K]` adds one lane (copying lane K's overrides) and starts only it. Ids are never reused. With faucet mode on, `lane add` is refused like `lanes=N` is: the faucet balance can't be split.
- `duckctl lane remove K` stops and disables only that lane: `Lane K stopped. Other lanes keep betting.` Lane 1 holds the base config and can't be removed.
- `duckctl lane rename K NAME` (or `--config-set laneK.name=NAME`): the running lane reloads and logs `[L3 friend-A]`, with the API key still masked. The lanes table gains a Name column once a lane has its own name.
- `duckctl start|stop|restart K` acts on one lane alone; plain `start|stop|restart` still act on every lane.
- `duckctl lanes N` starts or stops only the difference and never restarts `duckdice`. With the `bet`/`both` split the running lanes reload their base bet share; `budget`/`both` print `Budget shares update when each lane restarts: duckctl restart K.`
- The owner limit `lanes.max` (default 10, up to 99): `duckctl lanes --max N` or `--config-set lanes_max=N`. Lane ids go up to 99, for `--lane` and the `--stats --lane` filter too.
- New bot flags: `--lane-ids [enabled|all]`, `--lane-add NAME [--lane-from K]`, `--lane-remove K`.

### Pause, resume, reload
- `duckctl pause|resume|reload [K|all]` sends USR1, USR2 or HUP to lane K, or to every lane with a pid file (the default). The daemon already handled these signals; there was no command to send them.
- Only a pid whose `/proc/PID/cmdline` runs `duckdice.py` is signalled: a stale pid or another process is skipped with a reason, `No running lane matched.` exits 1, and a bad K exits 2 with the usage line.

### Tests
- `tests/test_lane_registry.py` (old shape read, `lanes=N` round trip, one-time backup, rename reloads one lane, add/remove, refusals write nothing, limit, ids above 10, `--lane-ids`, Name column, run reset, reload takes the count, an older casino-shared, the masked key on a named lane, lane add in faucet mode).
- `tests/test_duckctl_lanes.py` (add starts only the new unit, remove disables only its lane, rename, one-lane stop/start/restart, unknown lanes reach no unit, bad arguments exit 2, `--max`).
- `tests/test_duckctl_signals.py` (each verb reaches only the named lane, the default and `all` reach every running lane, stale, foreign and garbage pids are skipped, bad K exits 2).
- `tests/test_wolfbet_sync_e5_lanes.py`, `tests/test_wolfbet_sync_e7.py` and `tests/test_portability_v1.py` updated for the registry shape, ids up to 99 and the one-lane delta.

## duckctl v3.3 + installer v3.2 - wolfctl 3.12 operations (2026-11-23)

Mirrors wolfctl 3.12 and WolfBet's installer at CLI v2.37.2, as goatedctl 1.1 and cgctl 1.1 do. duckctl's `tg` commands (token in `~/duckdice-bot/.env`, `duckctl tg env`) and `web` are unchanged. `duckdice.py` is unchanged; every new command calls a flag it already has.

### Lanes
- `duckctl lanes [N] [--split none|budget|bet|both]` shows or sets the lane count. Lanes 2..N run as `duckdice-lane@K` and are enabled for boot; lanes above N are stopped and disabled. With the bot stopped, the kept lanes are only boot-enabled, so nothing starts while lane 1 is down.
- `start`, `stop`, `restart`, `start-all`, `stop-all` and `update` act on every lane; `status` adds the per-lane table when there is more than one; `monitor K` attaches to lane K.
- The `duckdice-lane@.service` template is written by the installer, and by `duckctl` when it is missing. Like the CLI unit it loads `EnvironmentFile=-~/duckdice-bot/.env`, so every lane gets the API key.

### Statistics
- `duckctl reset [--yes]` moves `~/.duckdice_autobot.db` to `~/.duckdice_autobot-<timestamp>.db` (never deletes it), removes the live-state files and the run files (`~/.duckdice_run*.json`) so the next start begins at session 1, and leaves the config alone. A running bot and its lanes are stopped, not restarted.
- `duckctl archives` lists the archived DBs with session counts, date spans and sizes.
- `duckctl run-reset [--yes]` starts a new run total for the run-wide profit increment and restarts the lanes that were running.

### Health and upkeep
- `duckctl doctor [--fix] [--yes]` checks the unit file, whether the CLI unit is enabled for boot (`[WARN]` with `systemctl --user enable duckdice`), the venv, the `casino-shared` import, config mode 0600, DB fragmentation and `integrity_check`, the journal, disk space, daemon priority and duckctl drift. On virtualized hosts the CPU governor advice is `[SKIP]`. `--fix` applies only safe fixes (enable the unit, chmod 600 the config, bet-row cleanup, VACUUM, journal vacuum); privileged ones are printed, never run.
- `duckctl update` installs the requirements once (an unguarded second `pip install` after the self-update is gone), pulls with `--autostash` and lists what it stashed, keeps the pinned requirements install (stops before any restart if it fails), installs the ctl with `install -m 0755`, warns `duckctl was stale: X -> Y` when the repo copy is newer, migrates `Restart=always` to `on-failure` and adds `Nice=-5` + IO priority to the CLI unit, and restarts the web admin, the Telegram bot and the CLI with its lanes only when they were running.
- `duckctl refresh` reinstalls duckctl from the repo copy in the current folder and prints the version change (no pull, no pip, no restart). `DUCKCTL_VERSION=3.3` is the value it and `update` compare.
- New pass-throughs: `version`, `config-show`, `config-set`, `cleanup [DAYS]`, `benchmark [N] [--lanes M] [--yes]`, `diagnose`, `last-bets [N]`, `preset NAME`.

### Installer
- The dead `../shared` copy is gone (casino-shared comes from the pinned requirements).
- The CLI unit runs at `Nice=-5` with best-effort IO priority; the lane template is written next to it.
- `systemctl --user enable duckdice` after `daemon-reload`, for boot only (no `--now`): a reboot brings the bot back, and a bot stopped by hand stays stopped.
- The installer ships the repo's duckctl with `install -m 0755` (it already had no heredoc copy, and still stops when duckctl is missing). Banner text uses plain ASCII dashes.

N/A: `pipeline` (duckdice has no `pipeline_depth`).

**Tests**: `tests/test_wolfbet_sync_e7.py` (59, the ctl run under bash against a temp HOME with stubbed systemctl and python). **490 total passing.**

## CLI v1.13.0 - exit reason, session export and offline backtest (2026-11-23)

Mirrors WolfBet CLI v2.20.0 (export), v2.21.0 (backtest) and v2.37.1 (exit reason), as goated CLI v1.13.0 and cryptogames CLI v1.13.0 do. Dice only.

### Why the bot stopped
- The exit summary has a `Stop reason` row: the final status, or `-` when there is none.
- The daemon logs `DAEMON EXIT  session=N  bets=B  profit=+P  reason=...` after the session is saved and before the last state write, so the saved `status` and the log agree and a stop is never a silent clean exit.

### Export
- `--export PATH [--export-session-id ID]` writes one session (default: the latest). A `.json` path gives `{"session": ..., "bets": [...]}`; anything else gives CSV with the session as `# key: value` lines over the bets table. Files are mode 0600.
- `E` in the TUI exports the current session (default `~/duckdice-session-<id>.csv`).
- `duckctl export PATH [SID]`.

### Backtest
- `--backtest --simulate N --seed S` bets virtually from the bet's own fields: win chance `bet_value` percent (`chance`), `over` is `isHigh`. The roll is a 0-100 percent roll, not duckdice's server number scale: `over` wins above 100 minus the chance, `under` below it; a win pays bet x (multiplier - 1). Results have `api_place_bet`'s shape (`bet`, `userBalance`), and the same seed gives the same result.
- `--backtest --replay-session ID` replays a stored session's win / loss sequence with the current strategy's bet sizes and payout.
- Config comes from `--preset NAME`, else the saved config, else the bot defaults (Martingale, 2.01x, over). The run is in memory on a 1.0 sandbox balance: no API call, no balance lookup, no DB write, no run file. With faucet mode on, the run is still virtual and touches no real faucet balance (the header says so).
- The live loop's stats update and next-bet code moved into `_record_bet_outcome` and `_next_bet_after`; the live loop and the backtest both call them, with the same WolfRider switch, rules and milestone hooks after each bet. Stop conditions use the same `should_stop`.
- `duckctl backtest [args]`.

N/A or not ported: duckdice's server roll scale and the real faucet balance (both need the network); WolfBet's Rich-panel session summary (the existing summary table carries the stop reason instead); recurring restarts inside a backtest (a stop ends it); WolfBet's later `--json` export report and multi-session backtest.

**Tests**: `tests/test_wolfbet_sync_e6.py` (26). **431 total passing.**

`VERSION` `1.12.2` -> `1.13.0`.

## CLI v1.12.2 - the base bet you set is the base bet it bets (2026-11-23)

Mirrors WolfBet CLI v2.37.2. A base saved while the bot ran never reached it, a restart re-inflated a new base by every bump earned on the old one, and `--config-show` could not show what the daemon was betting.

### A saved base reaches the running daemon
- `base_bet` is live over SIGHUP, so `--config-set basebet=...` and the wizard's save apply at once and no longer list it under "need a full restart".
- A base change drops the run's bumps, records the new base and re-anchors the next milestone one step above the run's current profit, so the next bump needs fresh profit. The current bet resets to the new base.
- A change is judged against this lane's configured base, not the live bumped one, so an unrelated reload (`--config-set maxloss=5`) keeps the bumps. WolfBet compares against the live base, which drops the bumps on any reload after a bump; that differs here on purpose.
- A resumed base with bumps still never goes below the learned casino minimum; a base you set is used as given.

### Bumps belong to the base they were earned on
- The run file stores `base`. On start, bumps earned on another base are dropped with a `RUN base changed` warning and the milestone re-anchors above run profit; on the same base they apply as before.
- A run file written before this version keeps its bumps once and adopts the current base.

### The screen shows the live value
- `--config-show` marks the `base_bet` row `0.00005805  (live 0.00006534, +729 bumps)` when a running daemon bets a different base.
- `duckctl start` on an active unit says `CLI bot was already running - nothing restarted.` with the `duckctl restart` hint.

N/A: WolfBet's auto-bet restart after a base change (no server-side auto-bet here). Known interaction, unchanged: with `basebet_pct > 0` the base is recomputed from the balance, so a hand-set base is transient there. No schema change; the Telegram bot is unchanged (it already keys bumps to their base).

`VERSION` `1.12.1` -> `1.12.2`.

## Security hotfixes: web, Telegram and limits (2026-11-15)

### Web admin
- `DUCKDICE_WEB_SECRET_KEY` and `DUCKDICE_WEB_ADMIN_PASS` must be non-default (32+ bytes, 12+ chars); the app exits before binding.
- The installer generates the secret, prompts for or generates the password, writes `.env` with `printf` at 0600, refuses a weak existing `.env`, and always binds uvicorn to `127.0.0.1`.
- Login lockout and single-use Telegram links are wired through the shared auth router.
- Every `/api/users/{user_id}/...` route uses `require_user_access`.
- `web/bot_db.py` exports the bound `_connect_ro(user_id)`, so the live session socket resolves.
- The admin user page renders this service's secret keys as `hidden`.

### Telegram
- `/update` answers only to `TG_OWNER_ID`, with no fallback id and a startup warning when it is missing; the launcher is an argv list for `systemd-run` with validated paths and a 0600 log under the data dir.
- `/set` and `/tweak` reject `nan` and `inf` for every numeric parameter, and the CLI config-set path uses the same check.

### Updates
- `duckctl update` installs the pinned requirements and stops when the install fails.

### Log redaction and the minimum-bet raise
- The Telegram log handler uses `RedactingFormatter`, every engine registers its key, and technical error text is redacted. `install.sh` writes `.env` 0600 and installs the repo's `duckctl` instead of its own stale copy.
- A 422 minimum bet is accepted only when it is finite, positive, at most 10x the configured base bet and at most a quarter of the balance; otherwise the bot stops and says so. The TG engine keeps the learned minimum as a floor and checks the raise against the balance.

**Version**: see the module constants; `casino-shared` pinned to the dashboard branch.

## CLI v1.12.0 + TG v1.10.0 — Run-wide profit increment (2026-11-15)

Mirrors WolfBet CLI v2.37.0 (`profit_increment_scope`, `0b7e71a`). The increment only measured the current session and the milestone reset every recurring cycle, so large thresholds were never reached with many short sessions. Helpers come from `shared.core.run_increment` (casino-shared 0.3.0,).

- **Scope** — `profit_increment_scope` (alias `piscope`): `session` (default, unchanged) measures the session's profit and its milestone resets each cycle; `run` measures the total since the run started and keeps the milestone across sessions. CLI: wizard asks after the increment prompts, `--config-set piscope=session|run` validates (exit 2) and needs a restart, `--config-show` lists it. TG: `/set piscope`, `/tweak piscope` (saved, applies at the next start), `/config`.
- **Run** — CLI per lane `~/.duckdice_run.json` / `.laneK.json` (env `DUCKDICE_RUN_PATH`), TG per user `run.json` next to the user DB: `{first_session, started_at, bumps}`. The finished sessions' profit is the DB sum (`sessions`, lane-filtered in the CLI); the current session is added. On start `base_bet = config base + bumps × increment` (never below a learned 422 minimum in the CLI; the TG bot keeps no minimum), next milestone `(bumps + 1) × threshold`. A run file past the DB's last session starts a new run. A recurring restart carries the bumped base and its bump count, so the bumps are never added twice.
- **Faucet** — DuckDice keeps no faucet flag on `sessions` and `state.profit` already counted faucet bets, so faucet sessions count in the run total exactly as they counted in session profit. No new column.
- **TG one slot** — a user's sessions carry no slot, so run scope runs one slot at a time: `/bet` is refused while another slot runs when the saved config or a running session holds run scope.
- **Off** — `pi` / `pt` = `none` / `off` / `0` / `0.0` (≤ 0) is fully off: no bumps, no run; the run file is removed at start or on live reload (CLI SIGHUP, TG `/tweak`). Re-enabling starts a fresh run at the current session.
- **Display / reset** — CLI `--status` `Run Profit` row, monitor `RunMs`, state file fields; `--run-reset [--lane K]`. TG `/status` run line; `/runreset` with a confirm button. The user DB `sessions` schema is unchanged.
- N/A: the WolfBet auto-bet UUID restart (no server auto-bet here; the bumped base applies to the next bet). `duckctl run-reset` / `reset` belongs to the ctl work.
- Tests: `tests/test_wolfbet_sync_e10.py` (37). The CLI and TG versions in `test_portability_v1.py` move to 1.12.0 / 1.10.0.

`VERSION` `1.11.0` → `1.12.0`; TG `VERSION` `1.9.0` → `1.10.0`.

## CLI v1.11.0 — Lane split minimum bet (2026-11-15)

Completes WolfBet CLI v2.36.0 (`lane_split`, `6174b96`) with the duckdice adapt from backlog Epic 9. The rest of v2.36.0 shipped in 1.10.0. Replays goated CLI v1.11.0. TG unchanged.

- **Minimum bet** — DuckDice has no fixed minimum: the CLI learns it per lane from a 422 "minimum bet" reply (`state.min_bet`). In `bet` / `both`, a split `base_bet` or `basebet_pct` bet below that learned minimum now stays full, with a warning in the log. Before, a split `basebet_pct` bet was divided and then raised to the minimum. Same on load and on SIGHUP reload; an untouched full bet is still saved as is. A lane's own `base_bet` override is never divided. Until a 422 is seen there is no minimum, so the split divides as in 1.10.0 (a split that rounds to 0 still stays full). An unsplit `basebet_pct` bet below the learned minimum is still raised to it. Uses `lane_config(..., min_bet)` and `split_base_bet` from casino-shared 0.3.0.
- **No default change** — duckdice 1.10.0 already shipped `lane_split=none` as the default, so lanes never had the always-on v2.35.0 budget guard here. The budget stop applies only with `lane_split=budget` or `both`, as before.
- N/A: nothing new.
- Tests: `tests/test_wolfbet_sync_e9.py` (14). `test_wolfbet_sync_e5_lanes.py::test_split_basebet_pct_divided` now expects the full bet below a learned minimum; `test_portability_v1.py` pins 1.11.0.

`VERSION` `1.10.0` → `1.11.0`.

## CLI v1.10.0 — Parallel lanes (2026-11-14)

Mirrors WolfBet CLI v2.35.0 (parallel lanes) and v2.36.0 (`lane_split`); where the two differ, v2.36.0 code wins. Lane helpers come from `shared.core.lanes` (casino-shared). Replays goated CLI v1.10.0. TG unchanged.

- **Lanes** — N independent bot processes on one account: `duckdice.py --daemon --lane K` (1–10). Each lane has its own session, chain, counters, stops, pauses and recurring / streak-reset cycle. Config file and stats DB are shared. Lane 1 keeps the classic state / pid paths, systemd unit and config writes; lane K > 1 uses `~/.duckdice_autobot_live.laneK.json` / `~/.duckdice_autobot.laneK.pid` (or `.laneK` on the `DUCKDICE_*_PATH` values) and logs with a `[LK]` prefix (API key still redacted). `--lane K` is applied before any command, so `--status` / `--monitor` / `--stop` / `--diagnose` act on that lane. Lane K's first bet waits `(K−1)·2 s`.
- **DB** — new `sessions` rows carry `lane`.
- **Money split** (`lanes.split`, `--config-set lane_split=MODE`): `none` (default: full base bet, no lane budget), `budget` (each lane may risk balance ÷ lanes), `bet` (base bet and `basebet_pct` ÷ lanes; a lane's own `base_bet` override is never divided; a split rounding to 0 stays full; a learned 422 minimum still floors `basebet_pct`), `both`. With a budget, the share is fixed at lane start and the lane stops with `Lane budget exceeded (next bet …, lane equity …)` when the next bet is larger than share + the lane's result across its sessions. That stop never restarts. The streak-reset chain-depth warning uses the lane share in `budget` / `both`. `save_config` writes the full base bet back when the split one is untouched.
- **422 min-bet raise** — stays per lane (each lane learns the minimum itself). The raised bet goes through the lane budget check before the next bet, so a minimum above the lane equity stops the lane instead of betting it.
- **Per-lane overrides** — `--config-set lanes=N`, `laneK.key=value`, `laneK.key=default` (also `unset` / `inherit`) write `lanes.count` / `lanes.overrides`. Values go through the normal `--config-set` validation (aliases, `mult` re-derives the chance). Shared-only keys are refused per lane: `api_key`, `currency`, `faucet`, `proxy`, `purge_days`, `db_write_mode`. A refused pair writes nothing. `save_config` keeps the `lanes` block; a lane ≥ 2 never writes the base config; SIGHUP reload merges the lane's overrides. A `laneK.*` change SIGHUPs that lane; a base `--config-set` SIGHUPs every running lane. `--config-show` prints the lane count, split mode and overrides.
- **Faucet + lanes refused** — the faucet balance can't be split. `--config-set lanes=N` (N > 1) with faucet on exits 2 and writes nothing; a start with lanes > 1 and faucet on exits 2 before any session; the `[F]` hotkey won't turn faucet on while lanes > 1.
- **`--benchmark N --lanes M`** — 1..M concurrent clients (each its own HTTP session, live proxy kept) fire N read-only `GET /bot/stats/{SYMBOL}` (key masked); req/s, scaling, p50, ok, 429s, then `recommend_lanes`. No rate-limit columns: DuckDice sends no quota headers.
- **`--lanes-status`** — one row per lane state file (state, session, uptime, run, profit, lane equity, bps) plus total bps and P/L.
- **Monitor `S` / systemd** — the stop uses this lane's unit (`duckdice` or `duckdice-lane@K`), so a systemd-managed lane is not respawned.
- N/A: soft pacing from quota headers (none sent; each lane keeps its 429 backoff), the cross-lane `/auto/start` lock (no server-side auto-bet), `pipeline_depth` as a forbidden key (no pipeline). `duckctl lanes` and the lane unit template come with E7.
- Tests: `tests/test_wolfbet_sync_e5_lanes.py` (66), incl. two lanes in threads on one fake account. `tests/conftest.py` sandboxes the lane base paths; `test_portability_v1.py` pins 1.10.0.

`VERSION` `1.9.0` → `1.10.0`.

## CLI v1.9.0 — Observability and tooling (2026-11-14)

Mirrors WolfBet CLI v2.17.0 and v2.30.3–v2.30.5 (metrics), v2.18.0 (wizard Step 5b + preset keys), v2.19.0 (`T` tweak), v2.22.1 / v2.32.1 (risky config + chain depth), v2.25.0 (`--config-show` / `--config-set` / `--version`), v2.26.0 (proxy, `purge_days`, `--cleanup`), v2.26.1 (`--benchmark` / `--diagnose`), v2.27.0 (async bet writer) and v2.28.1 (monitor footer, systemd-aware stop). TG unchanged.

- **Metrics** — API last / avg / peak / p50 / p99 and on-target % (with a bet delay), rolling cycle p50 / p99 / mean, recent BPS (1000 / cycle mean), theoretical max BPS and efficiency. Two new rows on the dashboard and `--monitor`; `--status` adds API, Cycle, Efficiency and DB writer rows. The state file gains the WolfBet metric keys plus `purge_days`, `proxy` (masked) and the writer stats.
- **`--benchmark [N]`** — N (default 20, 3–200) read-only `GET /bot/stats/{SYMBOL}` through the live session (proxy included); min / p50 / mean / p99 / max, theoretical BPS and the `casino-shared` verdict. No `--yes` needed (no bet). The balance read, the rate-limit probe and `--benchmark` share one URL builder (`_stats_url`). **`--diagnose`** — top blocker (network, CLI overhead, bet delay) and advice from the running daemon's state file.
- **Retention** — `purge_days` (1–30, default 3); startup purge and an hourly retention thread; `--cleanup [DAYS]` purges bet rows of old sessions and keeps the session rows.
- **Proxy** — `proxy` (http / https / socks5) on one `requests.Session` that every API call now uses; set at load (so `--resume` / `--daemon` get it), `--config-set`, tweak and SIGHUP; a bad saved proxy is dropped with a warning; credentials masked everywhere.
- **Async bet writer** — `db_write_mode` `async` (default; batches of 32 / 0.1 s, a full queue writes synchronously, drained before the session is saved on every exit) or `sync`.
- **Live editing** — wizard Step 5b (recurring, streak reset, `basebet_pct`, streak delays, milestones); `T` hotkey tweak with WolfBet's aliases (`_apply_mutation`); `--config-set KEY=VALUE …` saves, prints old → new and clamp warnings, refuses `api_key`, never writes a `DUCKDICE_API_KEY` env key to disk, and SIGHUPs a running daemon; `--config-show`; `--version`; `--help` examples. `_check_risky_config` (incl. the streak-reset chain-depth warning) shows in the wizard confirm step and logs at start. Strategy keys match WolfBet (2 / 3 / 6 WolfRider / 7 / 8), so no remap. A tweaked `base_bet` never goes below the casino minimum learned from a 422; a tweaked `multiplier` re-derives the win chance (`bet_value = 99 / multiplier`, as the wizard). Faucet stays on the `F` hotkey (not a tweak / SIGHUP key).
- **Monitor** — footer hint line and `Keys:` banner; `S` stops through `systemctl --user stop duckdice` when the daemon runs in `duckdice.service`, else SIGTERM.
- **Fix: API key leaks.** DuckDice puts the key in the URL query (`?api_key=`), and a failed request quoted it in the log, `last_error` (state file, `--status`, dashboard) and connect errors. A redacting log formatter (tracebacks too) and `_redact` on errors now mask it.
- **Fix: dead `--status` "Rate Limit" row** (read `rate_remaining`, which nothing wrote) removed.
- N/A: pipelining, the rate-limit-header / `/auto/start` quota rows, auto-bet UUID clearing, `bet_value` / HTTP 422 derivation from WolfBet. Parallel lanes and `--benchmark --lanes` are.
- Tests: `tests/test_wolfbet_sync_e5.py` (108). `tests/conftest.py` now patches `_http_session` and fails any unpatched live request; `test_portability_v1.py` pins 1.9.0.

`VERSION` `1.8.0` → `1.9.0`.

## CLI v1.8.0 — Stats windows, analytics, durations, lane filter (2026-11-14)

Mirrors WolfBet CLI v2.23.0 / v2.31.0 (`--analytics`, date-windowed), v2.30.9 (windowed totals), v2.33.0 (time-of-day window), v2.33.1 (wins / losses per session), v2.34.0 (time span, bet time, run time) and the stats / analytics part of v2.35.0 (lanes). Replays goated CLI v1.8.0. TG unchanged (it already has `/analytics`).

- **Fix: time-of-day window** — `--since "2026-11-13 14:00"` matched the whole day (`' ' < 'T'`). The window now comes from `shared.core.stats.stats_date_window`: the space becomes `T`, a bare `--until` date runs to `23:59:59.999999`, and a bad bound prints `Invalid --since/--until "…"` and no rows. The local `_stats_date_window` is gone.
- **`--stats` totals** — `Sessions: N (first → last)`, `Time Span … Bet Time …` (pauses between cycles count in the span, not in bet time), `Wins/Session` and `Losses/Session` (max / avg). All follow the window.
- **`--analytics [--since --until]`** (new, also `duckctl analytics`) — Overall panel (time range / span / bet time, sessions in profit / loss, win rate, wagered, P/L, best / worst session), By game, By strategy, Top 5 best / worst sessions. Reads only the `sessions` table. Faucet mode is not recorded on sessions or bets, so By game is a single "Dice" row.
- **`--lane K`** on `--stats` / `--analytics` filters `sessions.lane` (rows from before lanes count as lane 1); session headers show `[LK]` when K > 1; analytics shows "By lane" when the window holds more than one lane. `core/database.migrate_db` (CLI and TG) adds `sessions.lane` to existing DBs (`ensure_sessions_lane`); `--stats` / `--analytics` run the migration first. Until lanes run every new row is lane 1.
- **Run time** — dashboard and `--monitor` headers show `Run …` from `run_uptime_sec` (whole run, across cycles; absent for old state files); `--status` adds `Running for`.
- N/A: none.
- Tests: `tests/test_wolfbet_sync_e4.py` (27). `test_portability_v1.py` now calls the shared window helper and pins 1.8.0.

`VERSION` `1.7.0` → `1.8.0`.

## CLI v1.7.0 — Session cycles, streak reset, live reload (2026-11-14)

Mirrors WolfBet CLI v2.16.0 (recurring, streak delays, `streakbet_loss`, `basebet_pct`, milestones), v2.25.3 (`recurring_delay`), v2.28.2 (config persistence), v2.28.3 (SIGHUP reload), v2.28.4 (uptime freeze), v2.30.1 / v2.30.7 (`next_profit_milestone`, `Ms` window), v2.30.6 (delay cap), v2.32.0 (streak reset) and v2.32.1 (insufficient balance terminal). TG unchanged. DuckDice API calls unchanged.

- **Recurring cycles** — with `recurring` on, a stop on profit target / max bets / max wins (profit ≥ 0) closes the `sessions` row, pauses `recurring_delay` s (default 60), then opens a new row with fresh counters and streaks. The bumped `base_bet` is kept; `next_profit_milestone` re-arms from `profit_threshold`. Loss, balance-floor and insufficient-balance stops never restart. The pause stops early on `q` / SIGTERM and then opens no new row.
- **Faucet and minimum bet across cycles** — faucet mode stays on in the next cycle. A `base_bet` raised by a 422 "minimum bet" reply is kept, so the next cycle starts at the casino minimum; the minimum is remembered and `basebet_pct` never sets `base_bet` below it.
- **Streak reset** — `streak_reset_losses` N (0 = off) stops on the first win after N+ losses while profit ≥ 0 and restarts after `streak_reset_delay` s (default 600), **even when `recurring` is off**.
- **Streak pacing and sizing** — `streak_delay_loss` / `streak_delay_win` / `streak_delay_bets` ("N:sec") pause before the next bet; `streakbet_loss` ("N:mult") scales the next bet on every N-th loss in a row (never below `base_bet`); `basebet_pct` sets `base_bet` to that share of the balance every 10 bets; `milestone_bets/wins/losses/profit` log and show a milestone.
- **Uptime** — `uptime_sec` freezes at the last bet during a cycle pause, and `ended_at` uses that time, so bet time excludes the pause. New state-file fields: `run_uptime_sec` (never resets), `pause_remaining_sec`, `pause_total_sec`, `recurring`, `recurring_delay_sec`, `streak_reset_*`, `next_profit_milestone`. Dashboard and `--monitor` show `◐ PAUSED left/total`.
- **CFG row** — fix: `Ms` read `last_profit_milestone`, which was never set, and `Rec` never showed because the state file never wrote `recurring`. `Ms` now shows progress inside the current bump window from `next_profit_milestone`; new `SR nL→Ss` segment.
- **Persistence** — every key above is in the config file and presets; streak tuples are stored as `"N:X"` (null when off). `recurring_delay` and `streak_reset_delay` are clamped to 0..86400 on load and reload, with a message.
- **SIGHUP** reloads the live-safe keys (stops, pacing, recurring / streak reset, milestones, profit ramp, rules) without touching the session, counters or the bet ladder; other changed keys (faucet included: use `F`) are logged as needing a restart. SIGUSR1 / SIGUSR2 pause / resume unchanged.
- **Fix: insufficient-balance match** — any API error containing "balance" (HTTP-200 `error` body and the generic exception path) stopped the bot as insufficient. It now matches "insufficient" only. Insufficient balance stays terminal: no recurring or streak-reset restart.
- N/A: server auto-bet UUID clearing and the mid-chain auto guard (no server auto-bet), `--pipeline`, lanes (E5). The `martingale_chain_depth` warning lands with `_check_risky_config` (E5). No wizard step yet for the new keys (WolfBet Step 5b, E5): set them in the config file and send SIGHUP.
- Tests: `tests/test_wolfbet_sync_e3.py` (27); fake gains a `min_bet_422` outcome; `test_portability_v1.py` version pin.

`VERSION` `1.6.1` → `1.7.0`.

## TG v1.9.0 — Streak reset, delay caps, analytics window, 429 backoff (2026-11-14)

Mirrors WolfBet TG 1.20.0: v2.30.6 (delay cap), v2.32.0 (streak reset), v2.32.1 (insufficient balance terminal), v2.33.0 (time-of-day window). CLI unchanged. DuckDice API calls unchanged.

- **Streak reset** — `/set srloss N` (alias `streakreset`, `off` to disable) stops the session on the first win after N+ consecutive losses, only while profit ≥ 0. The bot then starts a new session (new `sessions` row) after `/set srdelay S` (alias `streakresetdelay`, default 600 s, 5–86400), **even when `recurring` is off**. Live `/set srloss|srdelay` carries into the next cycle; the bumped `base_bet` is kept, `next_profit_milestone` re-arms from `profit_threshold`, and faucet mode is kept. Uses `shared.core.session_rules` (`record_outcome`, `streak_reset_triggered`, `STREAK_RESET_REASON`).
- **Delay caps** — `/set recdelay` and `/set srdelay` clamp to 5..86400 and tell you when they clamp. A saved `recurring_delay` above 24 h is capped at the restart.
- **`/tweak`** gains `srloss` (live on the running engine) and `srdelay` / `recdelay` (saved; apply at the next stop).
- **`/config`, `/status`, stop message** show `Streak reset … / SR nL→Ss` when set; `/help` lists the keys; web settings page lists `streak_reset_losses` / `streak_reset_delay`; both keys are in `CONFIG_KEYS` (presets) and the `/resume` snapshot.
- **`/analytics`** builds its window with `shared.core.stats.stats_date_window`: `/analytics 2026-11-13 14:00` now starts at 14:00 instead of matching the whole day (`' ' < 'T'`). A time token after a date joins it, since Telegram splits on spaces. Bad bounds print the usage.
- **Fix: 429 backoff.** A 429 waited a fixed 2 s (plus the generic error backoff) and never escalated. It now waits `Retry-After` if sent, else 1, 2, 4 … s capped at 30 s, and stops with `Rate limit persisted >30min — giving up` after 1800 s of cumulative waiting (same as cryptogames TG). The wait checks `/stop` every 0.25 s; a placed bet resets the counters.
- **Fix: insufficient-balance match.** The generic exception path stopped the session on any error containing "balance". It now matches "insufficient" only. Insufficient balance stays terminal: no recurring or streak-reset restart.
- N/A for TG: none of the v2.32.0–v2.33.0 TG items depend on WolfBet-only API features.
- Tests: `tests/test_wolfbet_sync_e2.py` (35); fake gains `api_error_balance` and a `retry_after` header. `test_portability_v1.py` version pin and `/analytics` source check, and the E0 429 sleep counts, updated.

`tg/__init__.py` `1.8.1` → `1.9.0`.

## CLI v1.6.1 / TG v1.8.1 — casino-shared over HTTPS (2026-11-13)

Mirrors wolfbet v2.16.11 (casino-shared dependency installs over HTTPS). No bot behaviour change.

- **`requirements.txt`, `web/requirements.txt`: `casino-shared` via `git+https://github.com/snad1/casino-shared.git` instead of `git+ssh://`.** The repo is public, so pip fetches it anonymously; fresh servers no longer need a GitHub SSH key to run `install.sh`.
- **`duckctl update`** reinstalls casino-shared from the same HTTPS URL (it used `git+ssh` too).
- **Pinned to casino-shared `v0.2.0`** (`@v0.2.0`): the WolfBet sync helpers (`shared.core` session rules, stats, diagnostics, net, lanes and `sessions.lane`). `duckctl update` still reinstalls the latest `main`, as `wolfctl` does.

`VERSION` `1.6.0` → `1.6.1`. `tg/__init__.py` `1.8.0` → `1.8.1`.

## CLI v1.6.0 / TG v1.8.0 — Date-range stats + analytics (2026-08-15)

Mirrors the wolfbet v2.30.9/v2.31.0 date-window wave. No bet-loop behaviour change.

- **CLI `--stats --since/--until DATE`** — optional date window (bare `YYYY-MM-DD` or full ISO ts; bare `--until` is inclusive end-of-day) via new `_stats_date_window` helper. Both the session listing and the totals honor the window; filtered views raise the listing limit 20 → 50 and label headers `Range Totals`. `--since`/`--until` are modifiers on the standalone parser, not the mutually-exclusive command group.
- **`duckctl stats`** now passes extra args through: `duckctl stats --since 2026-08-01 --until 2026-08-15`.
- **TG `/analytics [SINCE] [UNTIL]`** — same window threaded through all five queries (lifetime, by game, by strategy, top-5 best/worst); header shows `Range analytics` + window label; usage hint on >2 args; `/help` updated.
- **Tests**: `tests/test_portability_v1.py` — 21 → 32 tests (helper units, seeded-DB functional filtering, argparse/source checks, `duckctl` passthrough).

`VERSION` `1.5.0` → `1.6.0`. `tg/__init__.py` `1.7.0` → `1.8.0`.

## CLI v1.5.0 / TG v1.7.0 — WolfBet hardening subset ported (2026-07-19)

Same portable subset as the cryptogames v1.5.0 port. No bet-loop behaviour change.

- **SQLite PRAGMAs** on both CLI `_db_conn` and TG `_get_conn` (synchronous=NORMAL, busy_timeout=5000, cache_size=-32000, temp_store=MEMORY).
- **`Restart=on-failure`** in `install.sh` for both units.
- **Uptime pause bookkeeping** — `session_bet_ended_at` + `session_pause_started_at`; `pause()` freezes, `resume()` clears; `get_status()` uptime uses frozen bet-end; `_db_save_session(final=True)` writes frozen `ended_at`. New `uptime_sec` + `pause_remaining_sec` in status dict.
- **Monitor Row 6** — placeholder `" P/L —"` replaced by `_build_config_progress_row(d)` (`Ms` / `MaxP` / `MaxL` / `MaxB` / `MaxW` / `MinBal` / `Rec`), green <80% / yellow ≥80% / bold_red ≥95%, fallback `CFG — no goals set`.
- **TG `/diagnose`** + **TG `/analytics`** + `/help` entries.
- **First tests**: `tests/test_portability_v1.py` — 21 tests.

`VERSION` `1.4.0` → `1.5.0`. `tg/__init__.py` `1.6.10` → `1.7.0`. `duckctl` unchanged.

## v1.6.10 — `/status` speed block trimmed (2026-06-17)

### Changed

- **Removed `Overhead` line** from the `/status` speed block — rarely actionable at a glance, adds noise.
- **`API stable` and `Efficiency` now render only when `bet_delay > 0`.** With no delay set, the theoretical-max reference is meaningless and median-vs-target has no target — both lines were pure clutter. When the user does set a delay, both lines render as before.

## v1.6.9 — Session-ended notification: green ✅ on profit, red 🛑 on loss (2026-06-17)

### Changed

- **`Session #X ended` notification now leads with `✅`** when the session ended with `profit >= 0`, keeping `🛑` for `profit < 0`. Previously every stop-notification opened with `🛑` regardless of outcome — a `max_profit` hit still triggered a red-icon push. Leading emoji is now driven by the sign of `s["profit"]`; every other line is unchanged.

## v1.6.8 — Fix: `/status` rendering as plain text — `bet_delay` opened an unclosed italic entity (2026-06-17)

### Fixed

- **`/status` rendered as PLAIN TEXT** (literal `*Session #X*`, `*Balance*`, `` `…` `` visible) because the v1.6.7 fallback was firing every invocation. Root cause: the literal token `bet_delay` on the "API stable" line sat OUTSIDE any code span — Markdown V1 read the `_` as the start of an italic entity, never found the closing `_`, and rejected the message. Renamed to `bet delay` (space, not underscore) — same meaning, no Markdown bomb. The v1.6.7 fallback remains in place as the safety net.

## v1.6.7 — Fix: `/status` silently failing on Markdown parse error (2026-06-17)

### Fixed

- **`/status` returns nothing in Telegram** when a rule description or engine status string contains an unbalanced Markdown character. `format_status` wraps user/external strings inside Markdown V1 code spans (`` `…` ``); a stray backtick splits the entity and Telegram silently rejects the message. Fixed at the render boundary — `_safe_code(s)` escapes backticks in the two leaky fields (rule descriptions, trailing engine status). Added `safe_reply_markdown(...)` wrapper for `/status` and `/monitor`; enhanced `_safe_send` and the monitor-refresh `edit_text` paths to retry as plain text on parse-error `BadRequest`. The user always sees the message; technical detail is logged server-side only.

## v1.6.6 — Fix: SQLite cross-thread error in _get_conn (defensive) (2026-06-17)

### Fixed

- **`DB flush_bets failed: SQLite objects created in a thread can only be used in that same thread.`** — `_get_conn` now opens directly with `sqlite3.connect(..., check_same_thread=False)` instead of via shared `db_connect`, so a stale pip-installed casino-shared can't reintroduce the bug.

## v1.6.5 — API stable: median + on-target % (2026-06-17)

### Added

- **`API stable: Xms median / Y% on-target`** — median = typical API time (unbiased by spikes); on-target % = bets where API ≤ bet_delay. Explains the Efficiency ceiling.

## v1.6.4 — Efficiency % indicator (2026-06-17)

### Added

- **`Efficiency: X%`** on the dashboard — bot's actual recent bps as a % of the theoretical max (= 1 / max(bet_delay, api_avg)). Single number that answers "is the bot at peak?".

## v1.6.3 — API peak time on the dashboard (2026-06-17)

### Added

- **`API: X last / Y avg / Z peak`** — third field tracks the slowest single API response observed this session. Spikes are permanently recorded so users can see WHY `Cycle` is elevated above `bet_delay`.

## v1.6.2 — Cycle metric: full per-bet time (bps reciprocal) (2026-06-17)

### Added

- **`Cycle: Xms/bet`** on the dashboard — exp-smoothed time between consecutive bet starts. This is the bps reciprocal (`Cycle × bps ≈ 1000`), shown inline as `(1000/385 = 2.60 bps)`. Complements `Overhead` (post-API processing only) — `Cycle` is the honest end-to-end number.

## v1.6.1 — Per-cycle overhead diagnostic + get_status() cache (2026-06-17)

### Added

- **`Overhead: Xms/bet`** on the dashboard — exp-smoothed time per bet OUTSIDE of `bet_delay` and the API call. Healthy = ~5–15ms; 50ms+ = investigate.
- **`get_status()` cache (250ms TTL)** — eliminates the brief per-second bucket dip at milestones.

## v1.6.0 — Sliding-window BPS + non-blocking SQLite writes (2026-06-17)

### Added

- **`Now` bps (sliding 60s window)** alongside the existing cumulative `Avg` — dashboard now shows both.

### Changed

- **DB writes no longer block the event loop.** `_flush_bets`, `_db_save_session`, and `cleanup_live_bets` now run via `asyncio.to_thread`. Inline batch-size flush trigger removed. Shared `db_connect` now opens with `check_same_thread=False`.

Result: multiple concurrent sessions scale closer to single-session throughput.

## v1.5.0 — Auto-revive: systemd retry-forever + external stall watchdog (2026-06-17)

### Added

- **`Restart=always` + `StartLimitIntervalSec=0`** on both CLI and TG bot services.
- **External stall watchdog timer** — `duckdice-tg-watchdog.timer` fires every 2 min; if `/tmp/duckdice-tg.heartbeat` is older than 180s, restarts `duckdice-tg`. Catches the stuck-but-alive case.
- **Heartbeat task** — `_heartbeat_loop()` writes the heartbeat file every 30s from `post_init`.

If the bot crashes, systemd brings it back in 10s. If it stalls, the watchdog brings it back within ~3 min. No more SSH-and-restart.

## v1.4.6 — /update cd's into repo dir before running ctl (2026-06-11)

### Fixed

- **/update failed with "No duckdice.py in current directory"** — `systemd-run` spawns bash with cwd=/. Now `cd /root/duckdice && duckctl update` is the bash command. Override via `BOT_REPO_DIR` env var if your layout is different.

## v1.4.5 — /update reports real errors + writes update log (2026-06-10)

### Fixed

- **/update silently failed when systemd-run errored** — Previous `subprocess.Popen` with `stderr=DEVNULL` swallowed failures. Now uses `subprocess.run --no-block` synchronously, captures stdout/stderr, surfaces real errors to Telegram. Update output goes to `/tmp/duckctl-update.log` so you can review what happened: `cat /tmp/duckctl-update.log`.

## v1.4.4 — /update Telegram command for self-update (2026-06-10)

### Added

- **`/update` Telegram command (owner-only)** — Pull latest code from git, copy files, reinstall `casino-shared`, restart the bot. Equivalent to running `duckctl update` over SSH, but triggered from Telegram. Owner gate via `TG_OWNER_ID` env var (defaults to your user ID if unset). Implementation spawns `duckctl update` via `systemd-run --user --collect` so the update survives the bot getting killed by the restart.

## v1.4.3 — Remove 5-session concurrency limit (2026-05-12)

### Changed

- **Unlimited concurrent sessions per user** — `MAX_SESSIONS = 5` cap removed from `/bet` and from the recurring-restart path. Users can now run as many concurrent sessions as they want (limited only by the casino's own API rate limits, which are shared across sessions).

## v1.4.2 — bet_delay is a minimum-interval, not a sleep-before (2026-05-11)

### Changed

- **`bet_delay` semantics** — Previously: `sleep(bet_delay)` before each bet, so cycle = `bet_delay + api_time`. Now: minimum interval since the previous bet *started*, so cycle = `max(bet_delay, api_time)`. With delay=1s and api=50ms, you get a clean 1 bet/sec; with api=1500ms there's no extra sleep so the bot keeps up. More predictable pace.

## v1.4.1 — Fix _safe_send infinite recursion (2026-05-02)

### Fixed

- **`_safe_send` infinite recursion** — The function was calling itself instead of `app.bot.send_message`, causing `RecursionError: maximum recursion depth exceeded` on every Telegram notification (stops, milestones, errors). Fixed to call `app.bot.send_message` directly.

## v1.4.0 — Recurring honors live config across all running sessions (2026-04-28)

### Fixed

- **Recurring only applied to sessions started AFTER `/set recurring on`** — Previously, `recurring_state` was registered at `/bet` time only if `config.get("recurring")` was true at that moment. If a user started a session, then later toggled recurring on and started another session, only the second one would auto-restart on stop — the first had no recurring entry. Now `recurring_state` is registered for every session unconditionally (per-slot snapshot of game/strategy preserved). At stop time the engine re-reads the user's saved config to decide whether to actually fire the restart, so toggling `/set recurring on/off` applies instantly to **every** running session regardless of game type.
- **Recurring lost across bot restart** — `load_resume_state` now also registers `recurring_state` for each resumed session so a stop after a bot update still triggers the configured restart.
- **Notification throttle now driven by live config** — The "is this a recurring session" check used for stop-message rate-limiting now reads the live `recurring` flag instead of the snapshot, matching the new behavior.

Per-session game/strategy isolation is preserved: each session's snapshot keeps its own game/strategy/rule etc., so a session restart stays the same game even with multiple games running concurrently.

## v1.3.0 — Streak Delay Bets (2026-04-21)

### Added

- **`streakdelay_bets`** — Delay every N total bets by X seconds, regardless of win/loss outcome. Format `N:seconds` (e.g. `/set streakdelay_bets 100:0.5` pauses 0.5s every 100 bets). Useful for throttling overall bet rate independent of streaks. Live-tweakable via `/tweak sdbets 100:0.5`. When multiple streak delays apply on the same bet (e.g. loss streak + bet count), the longest delay wins

## v1.2.2 — Recurring Notification Throttle (2026-04-21)

- **Telegram flood ban on fast recurring sessions** — Stop and start notifications are now rate-limited to at most one of each per 30 seconds per user during recurring cycles. Losses, errors, insufficient balance, and manual stops are always sent immediately regardless of throttle

## v1.2.1 — Recurring Restart Reliability & Task Safety (2026-04-21)

### Fixed

- **Fire-and-forget task exceptions now logged** — Added `_log_task_exception` done-callback to all `create_task` calls so unhandled exceptions are surfaced in logs instead of being silently swallowed
- **`_notify_stop` formatting errors caught** — Wrapped `format_stop` call in try/except; falls back to plain stop message so the bot never crashes on a bad status snapshot
- **`ensure_future` replaced with `create_task`** — All callback factories (`_make_on_stop`, `_make_on_milestone`, `_make_on_error`) now use `asyncio.create_task` with done-callbacks for proper error visibility
- **Recurring restart retries on engine-start failure** — When `engine.start()` returns false, the session is re-queued after the configured delay instead of being silently dropped
- **Monitor loop stays alive during restart gap** — `_monitor_loop` now continues iterating while a recurring restart timer is pending, showing "Recurring restart pending…" instead of prematurely exiting
- **Inline keyboard preserved during restart gap** — `refresh_status` callback keeps Refresh/Stop buttons visible with a pending message while waiting for the next recurring restart

## v1.2.0 — Adaptive Base Bet & Full Config Visibility (2026-04-14)

### Added

- **`basebet_pct`** — Set base bet as a fraction of current balance (e.g. `/set basebet_pct 0.001` = 0.1%). Recomputes on session start and at every profit-increment milestone, so base bet auto-shrinks during drawdowns and grows during profit. Live-tweakable via `/tweak bbpct 0.001`
- **`streakbet_loss`** — Reduce (or scale) bet after N consecutive losses. Format `N:multiplier` (e.g. `/set streakbet_loss 10:0.5` halves bet every 10 losses). Damping floor for Martingale-style strategies. Live-tweakable via `/tweak sbloss 10:0.5`

### Changed

- **Full config in every surface** — Session-start message, `/status`, and stop summary now display every active setting: streak delays, streak bet, basebet pct, profit bump + next milestone, milestone cadence, stops, rule/value, win/loss multipliers. Single `format_full_config` helper in formatter; no more drift between surfaces

## v1.1.4 — Insufficient Balance Stop (2026-04-13)

### Fixed

- **Insufficient balance now always stops session** — Added pre-bet balance guard (`current_bet > current_balance`) and broadened non-200 HTTP response detection. Previously, deep Martingale streaks could drive `current_bet` above balance without the session stopping

## v1.1.3 — Streak Delay & Recurring Bet Fixes (2026-04-09)

### Added

- **Streak delay** — Delay next bet after N consecutive wins/losses. Configure with `/set streakdelay_loss 5:1.0` (every 5 losses → 1s delay) and `/set streakdelay_win 10:0.5`. Works with all strategies including auto-bet. Live tweak with `/tweak sdloss 5:1.0` / `/tweak sdwin off`
- **Streak delay in /config** — Displays streak delay settings in configuration output

### Fixed

- **Recurring bet preserves profit increment** — When a session stops and recurring restarts, the current (profit-incremented) base_bet is now carried forward instead of resetting to the original config value. Useful for short recurring sessions to bypass server-side throttling while maintaining bet progression

## v1.1.2 — Bug Fixes (2026-04-07)

### Fixed

- **Live bet cleanup** — ISO timestamp `T` separator caused comparison failure with SQLite's `datetime()`. Bets older than purge_days now correctly deleted during running sessions
- **Telegram timeout on session start** — Session start confirmation wrapped in try/except. Session continues even if Telegram times out
- **Milestone bets=0** — Setting `milestonebets` to 0 now properly disables (was defaulting to 100)
- **Help text** — Added missing cleanup, delsession, purgedays commands

---

## v1.1.1 — Safety, Cleanup, Charts (2026-04-03)

### Added

- **Live bet cleanup** — Bets older than N days auto-deleted every hour during running sessions. `/set purgedays 1` (default 1, range 1-30)
- **Chart snapshots** — Profit/balance saved every 100 bets. Web charts survive bet cleanup
- **Profit increment = 0** — Creates new sessions at profit threshold without increasing base bet

### Fixed

- **CK audit** — 65 scientific notation violations fixed across all services
- **`/tweak basebet` safety** — No longer kills strategy recovery during loss streak (applies on next win)

---

## v1.1.0 — Async Engine, /tweak, Proxy (2026-04-02)

### Added

- **Async engine** — TG betting engine converted from blocking threads to async I/O. Multiple sessions run truly concurrent instead of competing for Python's GIL
- **`/tweak` command** — Live-edit running sessions without stopping: delay, stop conditions (maxwins, maxbets, maxprofit, maxloss, minbalance), milestones, basebet, multiplier, loss/win mult
- **Per-session proxy** — Route each session through a different IP: `/set proxy http://user:pass@ip:port` or `socks5://...`
- **Production-safe errors** — `_set_error()` method ensures raw API errors never leak to users in production

### Changed

- HTTP client: `requests.Session` → `httpx.AsyncClient`
- Threading: `threading.Thread` → `asyncio.create_task`
- Sleep: `time.sleep` → `asyncio.sleep`
- Callbacks: `run_coroutine_threadsafe` → `asyncio.ensure_future`
- Suppressed httpx/httpcore polling log noise

---

## v1.0.7 — Recurring Bets & Zero-Balance Start (2026-03-29)

### Added

- **Recurring bet feature** — Sessions auto-restart after configurable delay when stopped by conditions (profit target, max bets, etc). Configure with `/set recurring on|off` and `/set recurringdelay <seconds>`. Cancel with `/stop recurring`

### Fixed

- **Zero-balance TG sessions** — TG engine now allows zero-balance sessions when base bet is 0 (previously blocked with "No balance found")

---

## v1.0.6 — Fix BPM Calculation (2026-03-29)

### Fixed

- **BPM calculation** — `bets_per_minute` was set to raw count of bets in the current calendar minute instead of the actual rate; now correctly derived as `bets_per_second * 60`

---

## v1.0.5 — Insufficient Balance Fix (2026-03-29)

### Fixed

- **Insufficient balance detection** — CLI and TG now properly detect "insufficient balance" in API error responses and stop the session immediately
- **TG error notification** — Added `on_error` callback; sends `⚠️ Insufficient balance` message to user via Telegram before stopping

---

## v1.0.4 — Shared Library as pip Package (2026-03-29)

### Infrastructure

- **casino-shared pip package** — `shared/` library now installable via `pip install casino-shared` from GitHub, eliminating manual file copying on servers
- **requirements.txt** — Added `casino-shared @ git+ssh://git@github.com/snad1/casino-shared.git` to both bot and web requirements
- **install.sh** — Added `shared/` copy step as fallback for offline/non-pip installs
- **duckctl update** — Now syncs `shared/` library alongside core/tg during updates

---

## v1.0.3 — DRY Shared Library Extraction (2026-03-29)

### Infrastructure

- **Shared library extraction** — Extracted ~6,600 lines of duplicated code into `shared/` library used across all 6 casino bots
  - `web/websocket.py`, `web/database.py`, `web/auth.py`, `web/services.py`, `web/bot_db.py`, `web/routes/auth_routes.py` — thin shims delegating to shared modules
  - `tg/database.py` — thin shim binding `DATA_DIR` to shared persistence
  - `core/strategy.py` — keeps local STRATEGIES dict, delegates rule engine to shared
  - `core/database.py` — keeps local `init_db`, delegates utilities to shared
- Zero downstream import changes — all existing imports continue working

### Security

- **Shell injection fix** — Replaced `create_subprocess_shell` with `shutil.copy2` in update flow
- **Deprecated datetime fix** — Replaced `datetime.utcnow()` with `datetime.now(timezone.utc)`
- **Narrowed exception handling** — Replaced broad `except Exception` with specific types
- **Removed unused imports** — Cleaned up unused `import os` from shims

### Testing

- **114-test suite** added covering all shared modules

---

## v1.0.2 — Security Hardening (2026-03-29)

### TG Bot v1.0.2

#### Security

- **ENV-controlled error messages** — Error handlers now log full details server-side and show user-friendly messages in production. Controlled via `APP_ENV` environment variable
- **`duckctl` self-update safety** — Script wrapped in `{ }` block to prevent parse errors when `cmd_update` replaces the file mid-run

---

## v1.0.1 — Data Retention & Cleanup (2026-03-24)

### TG Bot v1.0.1

#### Added

- **Automatic bet cleanup** — Old bet records (3+ days) from ended sessions are automatically purged when a new session starts. Session statistics (profit, bets, streaks, balance extremes, speed) are preserved — only raw bet rows are deleted. Disk space is reclaimed via VACUUM.
- **`/cleanup [days]`** — Manually purge bet records older than N days (default 3). Session stats remain intact. Usage: `/cleanup` or `/cleanup 1`.
- **`/delsession <id>`** — Delete a specific session and all its bets by session ID. Blocks deletion of currently running sessions. Shows bet count and profit before confirming.
- **`bets_purged` flag** — Sessions table gains a `bets_purged` column to track which sessions have already been cleaned, avoiding redundant queries on subsequent cleanups.

## v1.0.0 — Initial Release (2026-03-15)

### Full Stack

- **CLI bot** (`duckdice.py`) — Interactive wizard, Pure ANSI TUI dashboard, daemon mode, monitor mode, presets.
- **Telegram bot** (`tg/`) — 27 commands, per-user isolation, multi-session (up to 5 concurrent), inline keyboards, auto-command registration.
- **Web dashboard** (`web/`) — FastAPI admin panel with JWT auth, per-user session views, CSV export, WebSocket live updates, audit log.
- **Shared core** (`core/`) — Single source of truth for strategies, database schema, and bet computation.
- **Server installer** (`install.sh`) — Systemd services, venv, `duckctl` management CLI.

### Features

- **8 strategies** — Flat, Martingale, Anti-Martingale, D'Alembert, Paroli, WolfRider, Delay Martingale, Rule-Based.
- **21 rule actions** — Bet amount, win chance, payout multiplier, switch, stop, reset.
- **12 currencies** — BTC, ETH, LTC, DOGE, TRX, BCH, XRP, USDT, BNB, USDC, SOL, XLM.
- **Dice game** — Over/Under prediction with customizable payout multipliers.
- **Smart stop conditions** — Max profit, max loss, max bets, max wins, min balance.
- **Named presets** — Save & load strategy configs by name.
- **Performance tracking** — BPS, BPM, peak/low speed, peak/low balance, best win, worst loss.
- **Multi-session support** — Up to 5 concurrent sessions per Telegram user.
- **Zero-downtime updates** — `duckctl update` with session auto-resume.
- **Benchmark command** — `/benchmark` to test API response speed.
- **Min bet error handling** — Auto-adjust base bet on 422 min bet errors instead of infinite retry.
- **Faucet support** — Free bets via DuckDice faucet endpoint.

### Post-Release Fixes

- Fix faucet balance display and add DECOY currency.
- Remove DECOY from currencies list.
- Handle 422 min bet error — auto-adjust base bet instead of infinite retry.
- Fix `AttributeError`: `delay_threshold` to `delay_martin_threshold` in TG engine.
- Add `/benchmark` command, API timing, and change password feature.
- Fix accessibility: add label associations and placeholders to change password form.
