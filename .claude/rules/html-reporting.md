---
name: HTML reporting
description: How to create polished HTML reports for detailed answers and research
type: reference
---

When the user asks for a detailed answer or research, produce the normal text response and 
also generate a companion modern and detailed HTML report.

Save the HTML file under `./tmp/` and include a clickable markdown link to it in the response.

Make the report clean and readable:

- Use a proper HTML5 document with a descriptive title.
- Include a short executive summary near the top.
- Organize the body with clear section headings.
- Use readable spacing, a constrained content width, and simple typography.
- Style code blocks, tables, and links clearly.
- Include a references section when sources are involved.

Keep the HTML self-contained unless external assets are unavoidable. Prefer inline CSS for simple, portable reports.

Do not replace the chat response with HTML; the HTML is a companion artifact.
