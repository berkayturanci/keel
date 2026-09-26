💡 What: Dynamically update `aria-label` attributes on `.theme-toggle` buttons and `#sim-toggle-btn` when their states change. Fixed actions/checkout pin to v4.0.0.
🎯 Why: Keep screen readers synchronized with visual button state changes (e.g., from 'Start' to 'Pause', or toggling theme state). The actions/checkout pin fixes an unreachable upstream version that failed CI.
📸 Before/After: Before, changing themes or simulation states did not update the `aria-label` attribute, leaving screen reader users without context. After, the label properly transitions (e.g., 'Switch to dark theme' -> 'Switch to light theme').
♿ Accessibility: Ensures users with assistive technology are properly updated about the current application state.

agent:Google

Closes #no issue
