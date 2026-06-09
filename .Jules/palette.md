## 2026-06-09 - Accessible Overflow Pre Blocks
**Learning:** For `<pre>` tags with `overflow-x: auto` containing long lines (like code snippets), keyboard-only users cannot scroll them horizontally unless the element is focusable. Adding `tabindex="0"` is essential for this interactivity, and pairing it with `:focus-visible` ensures mouse users don't see an unwanted outline while keyboard users get clear visual feedback.
**Action:** Always verify if elements with scrollable overflow areas are accessible via keyboard navigation.
