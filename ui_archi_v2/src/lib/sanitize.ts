/**
 * Shared HTML sanitizer for markdown-rendered content.
 * Allowlist approach: only safe tags survive, all attributes stripped
 * except href/target/rel on <a>.
 *
 * Used by: Chat.svelte, Thinking.svelte, Workspace.svelte, Feed.svelte
 * BUG-4 fix: previously only Chat had sanitization.
 */

const ALLOWED_TAGS = new Set([
    "A",
    "BLOCKQUOTE",
    "BR",
    "CODE",
    "EM",
    "H1", "H2", "H3", "H4",
    "HR",
    "LI",
    "OL",
    "P",
    "PRE",
    "STRONG",
    "UL",
]);

export function sanitizeHtml(html: string): string {
    if (typeof document === "undefined") return html;

    const template = document.createElement("template");
    template.innerHTML = html;

    const elements = Array.from(template.content.querySelectorAll("*"));
    for (const element of elements) {
        if (!ALLOWED_TAGS.has(element.tagName)) {
            element.replaceWith(
                document.createTextNode(element.textContent ?? ""),
            );
            continue;
        }

        for (const attr of Array.from(element.attributes)) {
            const name = attr.name.toLowerCase();
            if (element.tagName === "A" && name === "href") {
                const value = attr.value.trim();
                if (!/^(https?:|mailto:)/i.test(value)) {
                    element.removeAttribute(attr.name);
                }
                continue;
            }
            if (
                element.tagName === "A" &&
                (name === "target" || name === "rel")
            ) {
                continue;
            }
            element.removeAttribute(attr.name);
        }

        if (element.tagName === "A" && element.hasAttribute("href")) {
            element.setAttribute("target", "_blank");
            element.setAttribute("rel", "noopener noreferrer");
        }
    }

    return template.innerHTML;
}
