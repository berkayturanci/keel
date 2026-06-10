## 2026-06-10 - Screen reader noise from inline emojis
**Learning:** Decorative emojis in plain text (like ⚓ or 📊) are read aloud by screen readers, creating unnecessary noise.
**Action:** Wrap decorative text emojis in `<span aria-hidden="true">` to hide them from assistive technology.
