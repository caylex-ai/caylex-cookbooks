# Copy Tool Permissions

Copy one navigator's effective tool-permission policy from a model project to
matching navigator instances in customer projects.

## Prerequisites

- Python 3.10 or newer (standard library only)
- A Caylex platform access token with admin access
- Source and target projects connected to the intended navigator

```bash
export CAYLEX_PLATFORM_TOKEN="your_platform_access_token"
```

## Preview first

```bash
python3 copy_tool_permissions.py \
  --source-project-name "Your Model Project" \
  --target-project-name "Customer One" \
  --target-project-name "Customer Two" \
  --navigator-name "Your Navigator" \
  --dry-run
```

Review the JSON summary, especially each target's `unmatched` tools. Then omit
`--dry-run` to apply:

```bash
python3 copy_tool_permissions.py \
  --source-project-name "Your Model Project" \
  --target-project-name "Customer One" \
  --navigator-name "Your Navigator"
```

Project and navigator IDs are also supported with `--source-project-id`,
`--target-project-id`, and `--navigator-id`. Use `--require-complete-match` to
fail if any source tool is absent from a target. Use `--base-url` for another
environment.

## Matching and safety

The script matches the navigator itself by its tenant-level `navigator_id`.
Tools are matched by `tool_id` first. If IDs differ, it falls back to the stable
`(server_name, tool_name)` pair returned by the permission endpoint. Missing
tools are reported and skipped by default. Duplicate stable identities, missing
projects, missing navigator instances, invalid modes, and ambiguous names are
hard errors rather than guesses.

The update endpoint changes only the matched permissions included in each
request; unmatched target tools retain their current policy.

## Security caveats

- The platform token is read from `CAYLEX_PLATFORM_TOKEN` by default. Do not put
  it in command arguments or logs.
- Permission changes can grant tools execution rights. Always review a dry run,
  and consider `--require-complete-match` for tightly controlled rollouts.
- API errors are reported by method, path, and status without echoing the token
  or response bodies.

## Test

From this directory:

```bash
python3 -m unittest discover -s tests -v
```
