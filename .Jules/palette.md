## 2025-02-23 - Screen Reader Context for Scrollable Code Blocks
**Learning:** Adding `tabindex="0"` to `<pre>` elements makes their `overflow-x: auto` scrollable by keyboard (crucial for a11y), but it causes screen readers to focus the container without context.
**Action:** Always pair `tabindex="0"` on scrollable regions with `role="region"` and a descriptive `aria-label` (e.g., "Install code block") so screen reader users know what they've focused. Additionally, wrap decorative emojis and text arrows (like `→`) in `<span aria-hidden="true">` to prevent confusing readouts.
