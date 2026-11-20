# Frontend verification

Verified on 20 November 2026 in the Codex in-app browser with the real authenticated API and configured model.

## Visual reference and implementation

- Reference: [`frontend-concept.png`](frontend-concept.png), generated before implementation, 1488 × 1056.
- Desktop implementation: [`frontend-desktop.jpg`](frontend-desktop.jpg), full-page capture at a 1488 × 1056 viewport.
- Mobile question/answer: [`frontend-mobile.jpg`](frontend-mobile.jpg), full-page capture at a 390 × 844 viewport.
- Mobile action evidence: [`frontend-mobile-review.jpg`](frontend-mobile-review.jpg), same narrow viewport.
- Specification and intentional differences: [`frontend-spec.md`](frontend-spec.md).

The reference and latest implementation screenshots were opened for visual comparison. All shipped UI is native HTML/CSS and small SVG utility icons, not a screenshot overlay. The generated reference is documentation only; no raster production assets or external fonts are required.

| Fidelity anchor | Result |
| --- | --- |
| Header and navigation | Wordmark, product divider, identity, disconnect and left navigation follow the reference. Navigation becomes horizontal on mobile. |
| Layout | Composer, examples, answer and evidence follow the same hierarchy, spacing and alignment. |
| Typography | Desktop heading/header scale adjusted after screenshot comparison. System fonts keep installation and rendering simple. |
| Palette and surfaces | White, navy, teal and cool gray; restrained borders and no decorative illustration. |
| Content | Real canonical finding text, timestamps and provenance replace shortened mockup answer rows. Longer answers intentionally increase page height. |
| Responsive behavior | No page overflow at 390px, including an expanded evidence panel; evidence tables scroll within their bounded panel. |

## Functional checks

- Local launcher opens a tenant-bound session, strips the credential fragment, and loads the true device count and observation date.
- A live low-disk query returned two Acme devices with the expected 2.00% and 2.59% available disk space.
- Expanded evidence showed the formula, full source checksum, timestamp, field and source line 240 for the relevant MacBook snapshot.
- A live proposal request created one pending remediation-ticket simulation. Opening the confirmation and choosing Cancel left it pending.
- Confirming the reviewed simulation changed its status to **Simulated**, recorded the reviewer and cleared the pending count. No external action adapter exists.
- Mobile controls and review evidence remained usable. Browser console checks returned no warnings or errors.
- Switching from an authenticated review to an invalid credential cleared both rendered answers and the review list, removed the credential fragment, and required reconnection. Reconnecting restored only the authenticated tenant's workspace.
- The temporary viewport override was reset and the connected frontend left available to the user.

## Automated and package checks

- 39 Python tests passed; lint and formatting checks passed.
- Frontend tests verify public shell/assets do not expose tenant data or provider credentials, context requires authentication, query parameters cannot change tenant scope, and a second local launch preserves an existing session on a busy port.
- The built Python wheel includes the HTML, CSS and all three JavaScript modules. No frontend build or Node runtime is required for installation.
- Existing agent, isolation, evidence and approval tests remain green. The core agent was not changed by the frontend work.

These checks cover the assessment prototype and observed browser flows. They are not a formal accessibility audit or a claim of production readiness.
