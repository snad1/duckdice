# WolfBet sync parity: DuckDice

What this repo does compared to WolfBet CLI 2.16.0 to 2.37.2 and WolfBet TG v1.21.0, as of DuckDice CLI 1.13.0 and TG 1.10.1 (2026-11-23).

- **Ported**: same behaviour as WolfBet.
- **Adapted**: same intent, changed to fit this casino's API (the note says how).
- **N/A**: the casino API cannot support it; the note gives the API reason.

Paths are relative to this repo. Run the tests with `APP_ENV=testing .venv-test/bin/python -m pytest duckdice/tests -q` from the workspace root.

| WolfBet | Change | Status | Where | Test | Note |
|---|---|---|---|---|---|
| 2.16.0 | recurring restart, streak delays, `streakbet_loss`, `basebet_pct`, milestones | Ported | `duckdice.py:1688`, `duckdice.py:1637`, `duckdice.py:1661`, `tg/engine.py:87` | `tests/test_wolfbet_sync_e10.py::test_recurring_keeps_run_milestone_and_total` | TG already had it |
| 2.16.0 | 429 Retry-After backoff | Ported | `duckdice.py:1547` | `tests/test_wolfbet_sync_e0.py::test_cli_recovers_from_429_and_timeout` |  |
| 2.17.0 | API last / avg / peak / p50, on-target %, cycle, efficiency | Ported | `duckdice.py:775`, `duckdice.py:789`, `duckdice.py:836` | `tests/test_wolfbet_sync_e5.py::test_record_api_ms_counters_and_on_target` | no rate-limit row: N/A, the API returns no quota headers |
| 2.18.0 | wizard Step 5b + preset keys | Ported | `duckdice.py:3499` | `tests/test_wolfbet_sync_e3.py::test_preset_round_trip` |  |
| 2.19.0 | `T` tweak hotkey, `_apply_mutation` | Ported | `duckdice.py:1217`, `duckdice.py:2689`, `tg/handlers.py:1297` | `tests/test_wolfbet_sync_e5.py::test_apply_mutation_fields` | no UUID clear: no server auto-bet; TG already had `/tweak` |
| 2.20.0 | exit summary, CSV / JSON export, `E` hotkey | Ported | `duckdice.py:3900`, `duckdice.py:3725`, `duckdice.py:2662` | `tests/test_wolfbet_sync_e6.py::test_export_csv_shape_and_mode` |  |
| 2.21.0 | backtest / replay | Adapted | `duckdice.py:3752`, `duckdice.py:3838`, `duckdice.py:3769` | `tests/test_wolfbet_sync_e6.py::test_backtest_is_deterministic_by_seed` | dice only, service house edge |
| 2.22.0 | `core/games.py` WolfBet game registry | N/A | - | - | WolfBet API game list; each service keeps its own game builders |
| 2.22.1 | `_check_risky_config` | Ported | `duckdice.py:898` | `tests/test_wolfbet_sync_e5.py::test_risky_config_clean_for_safe_flat` |  |
| 2.23.0 | `--analytics` | Ported | `duckdice.py:4969`, `tg/handlers.py:1893` | `tests/test_portability_v1.py::test_cmd_analytics_exists` | TG already had `/analytics` |
| 2.24.0 | `--pipeline N` | N/A | - | - | single-request bet API; concurrent bets break balance tracking |
| 2.25.0 | max-nonce recovery | N/A | - | - | WolfBet seed / nonce API; the service has no max-nonce error |
| 2.25.0 | `--config-show`, `--config-set`, `--version` | Ported | `duckdice.py:5327`, `duckdice.py:5392`, `duckdice.py:5906` | `tests/test_wolfbet_sync_e10.py::test_config_set_scope` |  |
| 2.25.0 | monitor ROI / p99 | Ported | `duckdice.py:782`, `duckdice.py:4120` | `tests/test_wolfbet_sync_e5.py::test_cycle_p50_resists_outlier_p99_catches_it` |  |
| 2.25.1 - 2.25.2 | ctl subcommands, `--autostash` | Ported | `duckctl:1075`, `duckctl:782` | `tests/test_wolfbet_sync_e6.py::test_ctl_syntax` |  |
| 2.25.3 | `recurring_delay` | Ported | `duckdice.py:167` | `tests/test_wolfbet_sync_e2.py::test_recurring_keeps_recurring_delay` | TG already had it (no cap) |
| 2.26.0 | proxy, `purge_days` + `--cleanup` | Ported | `duckdice.py:23`, `duckdice.py:5593`, `duckdice.py:5612` | `tests/test_wolfbet_sync_e5.py::test_purge_days_default_clamp_off_alias` |  |
| 2.26.1 | `--benchmark`, `--diagnose` | Adapted | `duckdice.py:5681`, `duckdice.py:5758` | `tests/test_portability_v1.py::test_cmd_diagnose_exists` | probe = the service's read-only endpoint |
| 2.27.0 | async bet writer + PRAGMAs | Ported | `duckdice.py:2079`, `duckdice.py:2164` | `tests/test_wolfbet_sync_e5.py::test_writer_batches_land_and_stop_drains` | PRAGMAs were already there; TG already batched |
| 2.28.0 | helpers moved to `core/` | Adapted | `duckdice.py:15` | `tests/test_portability_v1.py::test_tg_analytics_parses_args_and_threads_where` | pure helpers live in casino-shared (`shared.core.*`) |
| 2.28.1 | monitor footer hints, systemd-aware stop | Ported | `duckdice.py:4089`, `duckdice.py:4640` | `tests/test_wolfbet_sync_e5.py::test_is_systemd_managed` |  |
| 2.28.2 | config persistence gap | Ported | `duckdice.py:292` | `tests/test_wolfbet_sync_e10.py::test_run_profit_sums_sessions_and_bumps_persist` |  |
| 2.28.3 | SIGHUP live reload | Ported | `duckdice.py:419`, `duckdice.py:477` | `tests/test_wolfbet_sync_e10.py::test_live_reload_increment_off_ends_run` |  |
| 2.28.4 | uptime freezes in recurring pause | Ported | `duckdice.py:728`, `tg/engine.py:116` | `tests/test_wolfbet_sync_e3.py::test_pause_freezes_uptime_and_reports_remaining` | TG already had it |
| 2.29.0 | profit increment restarts the auto cycle | N/A | - | - | no server auto-bet cycle; the bump already applies to the next bet |
| 2.30.0 | CFG row | Ported | `duckdice.py:3996` | `tests/test_wolfbet_sync_e10.py::test_progress_row_uses_run_total` | pre-existing |
| 2.30.1 | persist `next_profit_milestone`, reset per cycle | Ported | `duckdice.py:242`, `tg/engine.py:192` | `tests/test_wolfbet_sync_e10.py::test_recurring_keeps_run_milestone_and_total` | the CFG `Ms` segment now reads the persisted milestone |
| 2.30.2 | ctl update self-heal | Ported | `duckctl:1075` | `tests/test_wolfbet_sync_e5.py::test_apply_mutation_base_bet_updates_current_bet` |  |
| 2.30.3 - 2.30.5 | metric correctness, windowed cycle / efficiency | Ported | `duckdice.py:803`, `duckdice.py:836` | `tests/test_wolfbet_sync_e5.py::test_metric_helpers_empty` |  |
| wolfctl 3.7 - 3.8 | `doctor [--fix]`, `Nice=-5` | Ported | `duckctl:1127`, `install.sh:100` | `tests/test_wolfbet_sync_e7.py::test_doctor_warns_on_a_disabled_unit_and_loose_config` |  |
| 2.30.6 | `recurring_delay` cap 86400 + visible clamps | Ported | `duckdice.py:17`, `tg/handlers.py:30` | `tests/test_wolfbet_sync_e2.py::test_recurring_delay_capped_at_stop` | CLI and TG |
| 2.30.7 | `Ms` per-window progress | Ported | `duckdice.py:4014` | `tests/test_wolfbet_sync_e3.py::test_cfg_row_ms_window_rec_and_sr` |  |
| 2.30.8 | `bet_value` / 422 | N/A | - | - | WolfBet bet_value validation; DuckDice minimum-bet 422 is handled by the bounded minimum bet (security row) |
| 2.30.9 | ctl `reset` / `archives`, `--stats --since / --until` | Ported | `duckctl:1128`, `duckctl:1130`, `duckdice.py:5935` | `tests/test_wolfbet_sync_e7.py::test_reset_archives_and_never_deletes` |  |
| security 2026-11-15 | default web credentials refused, per-user web authz, WS `_connect_ro`, owner gate, `nan` / `inf` limits, pinned `ctl update`, admin secret rendering, API keys out of logs | Adapted | `web/config.py:12`, `web/bot_db.py:9`, `duckdice.py:246` | `tests/test_security_hotfix.py::test_other_user_is_forbidden` | same intent, service code paths |
| security 2026-11-15 | service-specific guards | Ported | `duckdice.py:246`, `duckdice.py:253` | `tests/test_wolfbet_sync_e5.py::test_redact_masks_key` | API key redaction, bounded minimum bet |
| 2.31.0 | date-range analytics | Ported | `duckdice.py:4681` | `tests/test_wolfbet_sync_e3.py::test_cfg_row_ms_window_rec_and_sr` | TG already had it |
| 2.32.0 | streak reset | Ported | `duckdice.py:18`, `tg/config.py:45` | `tests/test_wolfbet_sync_e2.py::test_streak_reset_fires_on_breaking_win_at_profit` | CLI and TG |
| 2.32.1 | insufficient balance is terminal on auto | Adapted | `duckdice.py:658`, `tg/engine.py:177` | `tests/test_wolfbet_sync_e0.py::test_cli_insufficient_balance_stops` | terminal stop + chain-depth warning; mid-chain auto guard N/A (no server auto-bet) |
| 2.33.0 | time-of-day window fix | Ported | `duckdice.py:47` | `tests/test_wolfbet_sync_e4.py::test_time_of_day_window_with_space` | CLI and TG |
| 2.33.1 | wins / losses per session | Ported | `duckdice.py:2246` | `tests/test_wolfbet_sync_e4.py::test_wins_losses_per_session` |  |
| 2.34.0 | durations, time span, run time | Ported | `duckdice.py:47` | `tests/test_wolfbet_sync_e4.py::test_time_span_vs_bet_time` |  |
| 2.35.0 | parallel lanes: `--lane K`, laneK paths, `[LK]` prefix | Ported | `duckdice.py:135` | `tests/test_wolfbet_sync_e5_lanes.py::test_apply_lane_switches_paths_and_log_prefix` |  |
| 2.35.0 | `sessions.lane` column | Ported | `core/database.py:14` | `tests/test_wolfbet_sync_e4.py::test_init_db_adds_lane_to_old_db` | schema lives in casino-shared `SESSIONS_SCHEMA` |
| 2.35.0 | split budget, `Lane budget exceeded` loss stop | Ported | `duckdice.py:1628`, `duckdice.py:52` | `tests/test_wolfbet_sync_e5_lanes.py::test_init_lane_budget` |  |
| 2.35.0 | per-lane overrides, `config-set lanes=N` / `laneK.key` | Ported | `duckdice.py:5526` | `tests/test_wolfbet_sync_e5_lanes.py::test_load_config_applies_lane_overrides` |  |
| 2.35.0 | lane start stagger | Ported | `duckdice.py:6098` | `tests/test_wolfbet_sync_e5_lanes.py::test_lane_start_stagger_and_budget` |  |
| 2.35.0 | soft pacing from `x-ratelimit-*` headers | N/A | - | - | the service returns no quota headers; each lane keeps the 429 backoff |
| 2.35.0 | `/auto/start` cross-lane lock | N/A | - | - | no server auto-bet |
| 2.35.0 | `pipeline_depth` forbidden key | N/A | - | - | no pipeline |
| 2.35.0 | `--benchmark --lanes M` | Adapted | `duckdice.py:5697` | `tests/test_wolfbet_sync_e5_lanes.py::test_benchmark_lanes_probes_read_only_and_recommends` | read-only probe at 1..M concurrent clients |
| 2.35.0 | `--stats` / `--analytics --lane K`, analytics By lane | Ported | `duckdice.py:4893` | `tests/test_wolfbet_sync_e4.py::test_analytics_by_lane_totals` |  |
| wolfctl 3.10 | ctl `lanes [N]`, all-lane start / stop / restart / update / reset | Ported | `duckctl:1109` | `tests/test_wolfbet_sync_e7.py::test_lanes_n_writes_config_and_enables_lane_units` |  |
| 2.35.0 | service-specific lanes | Adapted | `duckdice.py:121` | `tests/test_wolfbet_sync_e5_lanes.py::test_config_set_lanes_refused_with_faucet_on` | `lanes > 1` with faucet on is refused (faucet balance cannot be split) |
| 2.36.0 | `lane_split` none / budget / bet / both | Ported | `duckdice.py:359`, `duckdice.py:51` | `tests/test_wolfbet_sync_e5_lanes.py::test_config_set_lanes_split_and_overrides` | a divided bet below the service minimum stays full |
| wolfctl 3.11 | ctl `lanes N --split MODE` | Ported | `duckctl:196` | `tests/test_wolfbet_sync_e5_lanes.py::test_config_set_lanes_split_and_overrides` |  |
| 2.37.0 | `profit_increment_scope` / `piscope` session / run, run file, DB sum, resume bumps | Ported | `duckdice.py:1039`, `duckdice.py:1064`, `duckdice.py:84` | `tests/test_wolfbet_sync_e10.py::test_stale_run_file_after_db_reset_starts_new_run` | CLI; TG adapted by request |
| 2.37.0 | status `Run Profit`, monitor `RunMs` | Ported | `duckdice.py:4610`, `duckdice.py:4014` | `tests/test_wolfbet_sync_e10.py::test_progress_row_uses_run_total` |  |
| 2.37.0 | `--run-reset`, ctl `run-reset`, `reset` clears run files | Ported | `duckdice.py:1154`, `duckctl:1129`, `tg/handlers.py:1417` | `tests/test_wolfbet_sync_e10.py::test_run_reset_removes_files` |  |
| 2.37.0 | auto-UUID restart on bump | N/A | - | - | no server auto-bet; the bumped base applies to the next bet |
| 2.37.1 | UUID swap waits for non-negative profit and streak | N/A | - | `tests/test_wolfbet_sync_e5_lanes.py::test_min_bet_raise_within_equity_keeps_betting` | no server auto-bet, UUID or mid-chain guard; proven by the regression test instead |
| 2.37.1 | `Stop reason` row + `DAEMON EXIT ... reason=` | Ported | `duckdice.py:3921`, `duckdice.py:6187` | `tests/test_wolfbet_sync_e6.py::test_summary_shows_the_stop_reason` |  |
| 2.37.1 | `install.sh` enables the CLI unit (boot only) | Ported | `install.sh:209` | `tests/test_wolfbet_sync_e7.py::test_lanes_n_writes_config_and_enables_lane_units` | lane units too; `doctor` flags a disabled unit |
| 2.37.2 | bumps belong to the base they were earned on | Ported | `duckdice.py:1139`, `duckdice.py:351` | `tests/test_wolfbet_sync_e10.py::test_live_base_change_zeroes_bumps_and_reanchors` | live base reload judged against the configured base; run file keeps `base` |
| TG v1.21.0 | TG run-wide increment, parity with CLI 2.37.0 | Adapted | `tg/config.py:43`, `tg/handlers.py:1417` | `tests/test_wolfbet_sync_e10.py::test_tg_new_run_created_at_first_session` | WolfBet TG lacked it; added by request following v1.21.0 |

## Known gaps

Found by the cross-service regression suite (`tests/test_wolfbet_sync_e8_regression.py`); recorded, not fixed here.

- CLI user pause (SIGUSR1 / `p`) does not freeze session uptime; the regression test is a strict xfail.
- `test_wolfbet_sync_e5.py::test_benchmark_probes_balance_endpoint_and_masks_key` is timing-dependent and can flake.
