## 2026-06-12 - [Throttling Scroll Event Handlers]
**Learning:** Frequent scroll events without throttling trigger excessive reflows and block the main thread, especially on mobile devices. Directly placing DOM queries and updates in scroll handlers degrades animation performance.
**Action:** Always wrap scroll-bound DOM reads/writes in `requestAnimationFrame` with a ticking flag to batch operations to the browser's paint cycle.
