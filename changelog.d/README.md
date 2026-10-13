# Changelog fragments

One file per change: `changelog.d/<issue>.<type>.md`, where `<type>` is `added`,
`changed`, `deprecated`, `removed`, `fixed` or `security`. The file holds one or more
bullet lines (`- **Title** (#N). What changed.`). `make release-bump` collects them into
`CHANGELOG.md`; `make changelog-preview` shows the result. See `docs/keel/release.md`.
