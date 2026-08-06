# Public web product

The public dashboard in `web/` is a static React and TypeScript presentation of the deterministic `paic-public-demo` bundle. It has no backend, account, API credential, mutation endpoint, or runtime provider connection.

## Local use

```bash
.venv/bin/python -m paic.web_readiness build --workspace configs/tui/smoke.yaml --output-dir .artifacts/web-bundle
cd web
npm ci
npm run build
npm run preview
```

The browser reads only `data/bundle.json` copied from the validated export at build time. A schema/version mismatch produces an explicit unavailable state; missing values are displayed as unavailable rather than invented.

## Authority and privacy

The dashboard is observational. Remediation, approval, execution, rollback, evaluator scoring, and recovery decisions are historical source-bound records. The displayed source is deterministic synthetic data and is not a production-performance claim. Bundle strings are rendered as text only.

GitHub Pages serves static files and cannot attach repository-controlled response headers. CSP is therefore documented as a hosting limitation; this app uses no inline executable code, dynamic URL fetching, unsafe HTML, third-party fonts, analytics, or secrets. A custom-header capable host can add `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`.

## Deployment

`.github/workflows/pages.yml` generates and validates the bundle, builds the site, binds it to `GITHUB_SHA`, and deploys only pushes to `main` through the official Pages actions. Pull requests never deploy. The expected public URL is `https://kabbersokhi-boop.github.io/probabilistic-ai-incident-commander/` once Pages is enabled by a repository administrator.

## Accessibility

The shell provides landmarks, a skip link, semantic headings, keyboard navigable native controls, visible focus, mobile navigation, readable chart alternatives, reduced-motion support, and print styling. Both light and dark token sets are available from the theme selector.
