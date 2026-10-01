# AutoGame Localizer UI Integration v19

v19 focuses on UI product stabilization rather than new backend features.

## Changes
- WebView text-size stability: `text-size-adjust: 100%`, no proportional font scaling on resize.
- Network activity indicator: a thin non-blocking top progress bar for all API calls.
- Consistent blocking overlay for long-running Developer tasks.
- Confirmation modal focus management, Escape-to-close, backdrop click, and focus return.
- Reduced-motion and higher-contrast preferences.
- Compact-height behavior for small desktop windows without changing base text size.
- Existing Player / Developer / Editor workflows remain unchanged.

## Validation
- Existing pytest suite: 33 passed.
- UI runtime baseline: PASS.
- v15 runtime: PASS.
- v16 runtime: PASS.
- v17 runtime: PASS.
- v18 runtime: PASS.
- v19 runtime: PASS at 1000x700 and 1220x700.
