# Test fixtures for the v0.2 test suite.
#
# The tests don't actually invoke `gh app-create` (it would need browser
# interaction). Instead they:
#   - call _hermes_adapt / _hermes_verify directly on synthetic env text
#   - call _save_state / _load_state on tmp_path state files
#   - invoke the CLI as a subprocess with --dry-run, --json, --resume flags
#
# For the CLI tests, we use tmp_path/output_dir fixtures that look like
# a real .creds/ tree after a previous run (with .state.json + per-agent
# .env files) so we can exercise --resume and the consumers hermes path
# without needing a real gh invocation.
