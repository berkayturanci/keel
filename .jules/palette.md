## 2026-06-09 - Accessible Overflow Pre Blocks
**Learning:** For `<pre>` tags with `overflow-x: auto` containing long lines (like code snippets), keyboard-only users cannot scroll them horizontally unless the element is focusable. Adding `tabindex="0"` is essential for this interactivity, and pairing it with `:focus-visible` ensures mouse users don't see an unwanted outline while keyboard users get clear visual feedback.
**Action:** Always verify if elements with scrollable overflow areas are accessible via keyboard navigation.

## 2026-06-13 - Screen Reader Context for Scrollable Code Blocks and Semantic Symbols
**Learning:** Focusable scrollable code blocks need a specific accessible name; generic labels such as "Code snippet" make repeated regions hard to distinguish in screen-reader navigation.
**Action:** Pair each focusable `<pre>` region with `role="region"` and a distinct `aria-label` that names the content or task. Hide decorative link glyphs with `aria-hidden="true"`, but keep semantic arrows exposed when they convey workflow order.
