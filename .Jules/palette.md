## 2025-02-23 - Screen Reader Context for Scrollable Code Blocks and Semantic Symbols
**Learning:** Adding `tabindex="0"` to `<pre>` elements makes their `overflow-x: auto` scrollable by keyboard (crucial for a11y), but it causes screen readers to focus the container without context.
**Action:** Always pair `tabindex="0"` on scrollable regions with `role="region"` and a *distinct, descriptive* `aria-label` (e.g., "Install commands" instead of just "Code snippet") so screen reader users know exactly what they've focused. Additionally, while decorative emojis and arrows should be wrapped in `<span aria-hidden="true">` to prevent confusing readouts, *semantic* symbols (like an arrow representing "leads to" in a flow diagram) must remain exposed to assistive technologies to preserve the relationship meaning.

## 2025-02-23 - CI Evidence and Commit Head Updates
**Learning:** After applying a fix to a PR managed by `keel`, the evidence-verify CI job will fail until reviewers re-evaluate the new HEAD commit.
**Action:** Wait for review comments on the updated PR rather than modifying the CI pipeline.
