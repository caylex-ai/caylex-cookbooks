# Search skills by tag

Search exact skill tags across project-owned skills or Navigator Library global
skills. The script emits the API's complete response as a JSON array and uses
only Python 3.10+ standard library modules.

## Usage

```bash
export CAYLEX_PLATFORM_TOKEN="your_platform_access_token"

# One page across all projects
python3 search_skills.py project finance

# Restrict to projects by exact name and/or UUID; repeat --project
python3 search_skills.py project finance \
  --project "Support Team" \
  --project "6f1c0b3e-1e2a-4c9d-8f0a-2b7c5d9e1234"

# Search global skills and repeat --navigator for display names, API names,
# and/or UUIDs
python3 search_skills.py global finance \
  --navigator "Support Navigator" \
  --navigator "support_navigator"
```

Names, tags, and query values are URL-encoded by the script. Tag matching is
exact and case-sensitive. An omitted scope means all owners in the token's
tenant. You may also pass `--project all` or `--navigator all`; the backend
treats the presence of any case-insensitive `all` value as an all-owner search,
even if other scope values are present. Unknown explicit owners return `404`.

The default API root is `https://api.caylex.ai/api/v1`. Use `--base-url` before
the mode for another environment:

```bash
python3 search_skills.py --base-url "https://staging.example.com/api/v1" \
  project finance
```

## Pagination and JSON output

Both endpoints use 1-based `page` and `size` query parameters. `size` defaults
to 50 and must be between 1 and 200:

```bash
python3 search_skills.py project finance --page 2 --size 100
```

Responses are plain arrays with no total count or next-page envelope. Add
`--all-pages` to request consecutive pages, beginning at `--page`, until the
API returns fewer than `--size` items. The arrays are combined into one JSON
array on standard output:

```bash
python3 search_skills.py global finance --size 200 --all-pages > skills.json
```

## API contract

- Project mode calls
  `GET /skills?tag=...&project=...&page=...&size=...`.
- Global mode calls
  `GET /navigator-global-skills?tag=...&navigator=...&page=...&size=...`.
- `project` is repeatable and accepts an exact project name or UUID.
- `navigator` is repeatable and accepts an exact display name, API name, or
  UUID.
- Project search excludes navigator-owned skills. Global search excludes
  project-owned skills.
- Results include the owner identity and current-revision bundled-file
  manifest, but not the `SKILL.md` body.

Authentication is `Authorization: Bearer $CAYLEX_PLATFORM_TOKEN`.

## Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```
