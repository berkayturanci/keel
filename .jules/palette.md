## 2026-06-09 - Accessible Overflow Pre Blocks
**Learning:** For `<pre>` tags with `overflow-x: auto` containing long lines (like code snippets), keyboard-only users cannot scroll them horizontally unless the element is focusable. Adding `tabindex="0"` is essential for this interactivity, and pairing it with `:focus-visible` ensures mouse users don't see an unwanted outline while keyboard users get clear visual feedback.
**Action:** Always verify if elements with scrollable overflow areas are accessible via keyboard navigation.

## 2026-06-13 - Screen Reader Context for Scrollable Code Blocks and Semantic Symbols
**Learning:** Focusable scrollable code blocks need a specific accessible name; generic labels such as "Code snippet" make repeated regions hard to distinguish in screen-reader navigation.
**Action:** Pair each focusable `<pre>` region with `role="region"` and a distinct `aria-label` that names the content or task. Hide decorative link glyphs with `aria-hidden="true"`, but keep semantic arrows exposed when they convey workflow order.

## 2024-06-21 - Syncing aria-current with active classes
**Learning:** When building single-page/client-side navigation that toggles an `on` or `active` visual class via JavaScript, relying solely on CSS classes means screen readers miss the update.
**Action:** Always sync the `aria-current="page"` attribute synchronously with the active class changes in JS to ensure robust accessibility for navigation items.
## 2024-06-22 - Sync aria-current with single-page navigation active view state
**Learning:** In custom single-page applications, updating visual `.active` or `.on` classes for navigation links is insufficient for screen readers. The `aria-current="page"` attribute must be added or removed dynamically alongside visual cues for navigation links managed by JavaScript (e.g. IntersectionObserver or click handlers).
**Action:** When updating active view states dynamically in JavaScript, always add or remove the `aria-current="page"` attribute in sync with the visual class change to maintain full screen reader accessibility.

## 2024-06-25 - Syncing ARIA attributes for dynamic toggle states
**Learning:** For dynamic toggle UI elements (like segmented controls), visually toggling an `.on` state is not enough; the corresponding ARIA attribute must be updated. For toggle buttons, this should be `aria-pressed="true"`.
**Action:** When adding `.on` or `.active` dynamically to a UI toggle button via JavaScript (e.g. `classList.toggle("on", isActive)`), always set `aria-pressed="true"` when `isActive` is true, and remove it (or set to "false") when false to correctly convey state to screen reader users.

## 2026-06-15 - ARIA Labels on Buttons with Generic Text
**Learning:** Copy buttons that just contain generic text like "copy" lack context for screen reader users when navigating interactively. Adding an `aria-label` provides a much more descriptive string.
**Action:** Always add descriptive `aria-label`s to action buttons that rely solely on generic text strings (like "copy", "read more"), especially in technical documentation.

## 2026-06-25 - Avoid ARIA Live on focusable elements
**Learning:** Applying `aria-live="polite"` directly to interactive or focusable elements (like buttons) is an accessibility anti-pattern. It can cause duplicated or disrupted screen reader announcements. For instance, if a button with a static `aria-label` dynamically changes its inner text (e.g. from "Copy" to "Copied"), the accessible name often doesn't change, meaning the state update is missed anyway.
**Action:** Instead of applying `aria-live` to the button, use a single, shared off-screen `.sr-only` container with `aria-live="polite"` to announce success states (e.g., "Copied to clipboard").

## 2026-06-25 - Explicit Labels over aria-label for Search Inputs
**Learning:** Using `aria-label` directly on a form input is technically accessible, but creating a visually hidden `<label class="sr-only">` explicitly associated with the input (`for="id"`) is the gold standard for screen readers and improves backwards compatibility. This is especially true for global elements like site search.
**Action:** Always prefer `<label for="...">` with `.sr-only` class over `aria-label` for standalone form inputs like search bars to ensure robust screen reader support.

## 2026-06-25 - SVG Icons and aria-hidden
**Learning:** Screen readers may redundantly announce SVG structures when they are used as decorative icons inside elements that already have an accessible name (like an `aria-label` or text content on a button).
**Action:** Always add `aria-hidden="true"` to decorative `<svg>` tags (like icons) to reduce screen reader noise.

## 2026-07-26 - `role="tab"` is a contract, not an attribute swap
**Learning:** Swapping `aria-pressed` for `aria-selected` and adding `role="tablist"`/`role="tab"` to a single-select list makes accessibility *worse* if the behaviour behind the roles is missing. Once an element is a tab, assistive tech announces "tab, 3 of 16" and users reach for Arrow keys; without a roving tabindex every tab also stays in the Tab sequence, and without `role="tabpanel"` + `aria-controls`/`aria-labelledby` there is no announced route from a tab to the content it selected. A plain button with `aria-pressed` is better than a tab that does not behave like one.
**Action:** Only adopt `role="tab"` together with all four parts: (1) roving tabindex — selected tab `tabindex="0"`, the rest `-1`; (2) Arrow/Home/End handling on the tablist; (3) a `role="tabpanel"` wired both ways; (4) valid tablist children. For a single-select list that is *not* a tab/panel pair, use `aria-current` instead; for a settings segmented control, use `role="radiogroup"` + `aria-checked`, not `aria-pressed`.

## 2026-07-26 - A tablist may only own tabs
**Learning:** `role="tablist"` permits only `tab` children. Interleaved group headings (`<div class="st-group">`) become invalid children and are liable to be dropped from the accessibility tree — silently losing the grouping that gives a long tab list its structure.
**Action:** Mark the visual heading `role="presentation"` and fold its text into each tab's accessible name (`aria-label="<group>: <name> — <one-liner>"`), so the grouping survives for screen reader users without relying on `display: contents` or `aria-owns` support.

## 2026-07-26 - Segmented settings are radiogroups, not toggle rows
**Learning:** A segmented control that picks exactly one value (Motion: Balanced | Max) is a single-select group. Marking each segment with `aria-pressed` describes it as a row of independent toggles, so a screen-reader user is told two buttons can be "pressed" at once and never learns the options are alternatives. `aria-current` is not the fix either — that marks the current item in a navigational set, not a chosen setting value.
**Action:** Use `role="radiogroup"` on the container with an `aria-labelledby` pointing at its visible label, `role="radio"` + `aria-checked` on each segment, and the same roving-tabindex + Arrow/Home/End contract a tablist needs — the group is one Tab stop, arrows move and select within it.

## 2026-07-26 - A popover trigger owes aria-expanded and focus return
**Learning:** A disclosure button that opens a popover must report its state via `aria-expanded` and point at the panel with `aria-controls`, otherwise a screen-reader user cannot tell the popover exists or whether it is open. Closing with `Escape` without moving focus is equally broken: focus is left inside a now-hidden container.
**Action:** Toggle `aria-expanded` in the same place the open class is toggled (one helper, never two code paths), and on `Escape` return focus to the trigger that opened the popover.
