# Fleet Copilot frontend reference

Direction: a compact utility UI served by the existing Python app; no build tool or extra frontend framework is needed for these two small screens. Built-in Image Gen produced `frontend-concept.png` before implementation. It is a design reference, not a shipped page image.

Prompt brief: full desktop Rayda Fleet Copilot screen, white/navy/teal, 76px header, left navigation, fleet question composer, three examples, evidence-backed answer rows, and expandable agent steps. No marketing sections, charts, fabricated metrics, or illustrations. The source mockup is 1488 × 1056.

Tokens: white background #fff, cool-gray sidebar #f5f7fa, navy text #142638, secondary #626b77, teal action #10756d, selected navigation #e0f0ed, border #d9dee5, 5px controls, 8px composer. System sans-serif. At desktop widths of 1200px and above: 48px heading, 26px subtitle, 20px controls, 92px header and 256px sidebar, with 40px content gutters. Smaller desktop widths use a 34px heading, 18px subtitle, 76px header and 205px sidebar. Below 760px, the heading is 28px, content gutters are 20px, the header wraps and navigation becomes horizontal. Controls wrap without page overflow.

Allowed primary copy: rayda; Fleet Copilot; company name; role; Disconnect; Copilot; Action reviews; Actions are simulated; Your fleet, explained.; Ask a question. Check the evidence. Decide what happens next.; dynamic device count and latest observation; Ask about your fleet; Which devices are low on disk space?; Allow action proposals; Ask copilot; Low disk space; Compliance checks; Memory trends; Answer; Based on recorded telemetry; View evidence; Agent steps.

Necessary workflow extensions to the reference: connection screen, loading and errors, real canonical answer wording instead of mockup abbreviations, review queue, refresh button, proposal details, a confirmation dialog showing the exact digest, and empty states. These implement existing backend behavior in the same visual system. Metrics and findings are always populated from authenticated API responses, never seeded from the design image. Evidence and data-derived strings are rendered as text, never HTML.

No production raster assets are required. All controls, text, borders and utility arrows are native HTML/CSS or small inline SVG icons. The brand is a text wordmark.
