## Agent skills

### Issue tracker

Issues and specs live as GitHub issues in `zhangzhefang-github/ontology-vkg-demo`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Decision loop

Supply-review code is `decision/`, `sql/decision-schema.sql`, and `compose.decision.yml`. When those paths are absent, run `git branch` and `git worktree list` before reporting the loop as unbuilt.
