## 2026-06-09 - Accessible Overflow Pre Blocks
**Learning:** For `<pre>` tags with `overflow-x: auto` containing long lines (like code snippets), keyboard-only users cannot scroll them horizontally unless the element is focusable. Adding `tabindex="0"` is essential for this interactivity, and pairing it with `:focus-visible` ensures mouse users don't see an unwanted outline while keyboard users get clear visual feedback.
**Action:** Always verify if elements with scrollable overflow areas are accessible via keyboard navigation.

## 2026-06-13 - Screen Reader Context for Scrollable Code Blocks and Semantic Symbols
**Learning:** Adding `tabindex="0"` to scrollable `<pre>` regions makes them keyboard-accessible, but screen readers also need distinct context for each focused region. Decorative symbols can be hidden from assistive technologies, but semantic symbols such as arrows that express workflow order must remain exposed.
**Action:** Pair focusable scrollable code regions with `role="region"` and a distinct `aria-label`. Hide only decorative symbols with `aria-hidden="true"`; leave semantic flow arrows readable.
