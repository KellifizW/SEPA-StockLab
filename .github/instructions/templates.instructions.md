---
name: 'Template Standards'
description: 'Bootstrap 5 dark theme and Jinja2 template conventions'
applyTo: '**/templates/**'
---
# Template Conventions for SEPA-StockLab

## Bootstrap 5 Dark Theme
- Use `data-bs-theme="dark"` on `<html>` tag
- Use CSS custom properties from `base.html` (e.g., `--accent`, `--success`, `--danger`)
- Do NOT add random inline styles — use existing CSS custom properties or Bootstrap utilities

## Bilingual Text
All user-facing text should be bilingual where feasible:
```html
<h5>掃描結果 Scan Results</h5>
<span>趨勢 Trend</span>
```
Primary: Traditional Chinese (繁體中文). Secondary: English.

## JavaScript
- All JS stays inline in templates — no external `.js` files, no bundler
- Use `fetch()` for API calls with proper error handling
- Poll background job status with `setInterval`:
```javascript
const poll = setInterval(async () => {
    const res = await fetch(`/api/scan/status/${jobId}`);
    const data = await res.json();
    if (data.status === 'done' || data.status === 'error') clearInterval(poll);
}, 2000);
```

## Layout
- All pages extend `base.html` with `{% extends "base.html" %}`
- Content goes in `{% block content %}...{% endblock %}`
- Use `.container-fluid.px-4.py-3` for page content wrapper

## Toast Notifications
Use the global `showToast(msg, type)` function from `base.html` for user feedback.
Types: `'success'`, `'danger'`, `'warning'`, `'info'`.
