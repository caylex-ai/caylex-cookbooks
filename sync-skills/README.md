# Sync skills

Reconcile a repository directory of skills with either one Caylex project or
one Navigator Library navigator. The script uses only Python 3.10+ standard
library modules.

Each immediate child directory must contain a `SKILL.md`. The skill identity is
read from the top-level `name` field in its YAML frontmatter, not from the
directory name. No YAML package is required. If a skill directory has files
besides `SKILL.md`, the script creates an in-memory ZIP whose archive root
contains `SKILL.md` and the bundled files; otherwise it uploads `SKILL.md`
directly.

## Usage

```bash
export CAYLEX_PLATFORM_TOKEN="your_platform_access_token"

# Project-owned skills
python3 sync_skills.py project "Production Project" ./skills

# Global skills owned by a Navigator Library navigator
python3 sync_skills.py global "Support Navigator" ./global-skills
```

The default API root is `https://api.caylex.ai/api/v1`. Override it for another
environment:

```bash
python3 sync_skills.py project "Production Project" ./skills \
  --base-url "https://staging.example.com/api/v1"
```

Project and navigator names are exact and case-sensitive. The script URL-encodes
owner names and skill identifiers. The backend's by-name path routes cannot
address an owner name containing `/` (percent-encoding does not avoid router
path splitting), so the script rejects that case with a clear error.

## Safe pruning and previews

Remote skills are never deleted by default. Add `--prune` only when the local
directory should be authoritative:

```bash
# Preview every add, update, and removal. GET requests are still made.
python3 sync_skills.py project "Production Project" ./skills --prune --dry-run

# Apply the same reconciliation, including removals.
python3 sync_skills.py project "Production Project" ./skills --prune
```

`--dry-run` suppresses every `POST`, `PUT`, and `DELETE`. Without `--prune`, it
previews additions and updates only. The script pages through the remote list
with `page` and `size=200` so pruning decisions include every remote skill.

## API contract

Project mode uses:

- `GET|POST /projects/by-name/{project_name}/skills`
- `PUT|DELETE /projects/by-name/{project_name}/skills/{skill_name_or_slug}`

Global mode uses the canonical equivalents:

- `GET|POST /navigator-global-skills/by-name/{navigator_name}/skills`
- `PUT|DELETE /navigator-global-skills/by-name/{navigator_name}/skills/{skill_name_or_slug}`

List responses are plain JSON arrays. `POST` creates and returns `409` for a
collision; `PUT` replaces an existing skill and returns `404` when absent. The
backend accepts exactly one multipart `file` containing either raw `SKILL.md`
or a ZIP bundle. Authentication is `Authorization: Bearer
$CAYLEX_PLATFORM_TOKEN`.

## Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```
