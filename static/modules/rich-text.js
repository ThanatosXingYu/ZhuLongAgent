const RICH_TEXT_TAGS = new Set([
  "A",
  "B",
  "BLOCKQUOTE",
  "BR",
  "CODE",
  "DIV",
  "EM",
  "H1",
  "H2",
  "H3",
  "H4",
  "HR",
  "I",
  "IMG",
  "LI",
  "OL",
  "P",
  "PRE",
  "S",
  "SPAN",
  "STRONG",
  "TABLE",
  "TBODY",
  "TD",
  "TH",
  "THEAD",
  "TR",
  "U",
  "UL",
]);

const DROPPED_RICH_TEXT_TAGS = new Set([
  "BUTTON",
  "EMBED",
  "FORM",
  "IFRAME",
  "INPUT",
  "MATH",
  "OBJECT",
  "SCRIPT",
  "STYLE",
  "SVG",
  "TEXTAREA",
]);

export function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function icon(name) {
  const node = document.createElement("i");
  node.dataset.lucide = name;
  node.setAttribute("aria-hidden", "true");
  return node;
}

export function refreshIcons() {
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons({ attrs: { "stroke-width": 1.8 } });
  }
}

export function renderRichText(raw, className, safeURL) {
  const container = createElement("div", className);
  const documentNode = new DOMParser().parseFromString(String(raw || ""), "text/html");
  if (![...documentNode.body.childNodes].some((node) => node.nodeType === Node.ELEMENT_NODE)) {
    container.classList.add("is-plain");
  }
  documentNode.body.childNodes.forEach((node) => appendRichTextNode(container, node, safeURL));
  return container;
}

function appendRichTextNode(parent, source, safeURL) {
  if (source.nodeType === Node.TEXT_NODE) {
    parent.append(document.createTextNode(source.textContent || ""));
    return;
  }
  if (source.nodeType !== Node.ELEMENT_NODE) return;
  const tag = source.tagName.toUpperCase();
  if (DROPPED_RICH_TEXT_TAGS.has(tag)) return;
  if (!RICH_TEXT_TAGS.has(tag)) {
    source.childNodes.forEach((child) => appendRichTextNode(parent, child, safeURL));
    return;
  }
  if (tag === "A") {
    const href = safeURL(source.getAttribute("href"));
    if (!href) {
      source.childNodes.forEach((child) => appendRichTextNode(parent, child, safeURL));
      return;
    }
    const link = document.createElement("a");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    if (source.getAttribute("title")) link.title = source.getAttribute("title");
    source.childNodes.forEach((child) => appendRichTextNode(link, child, safeURL));
    parent.append(link);
    return;
  }
  if (tag === "IMG") {
    const src = safeURL(source.getAttribute("src"));
    if (!src) return;
    const image = document.createElement("img");
    image.src = src;
    image.alt = source.getAttribute("alt") || "题目图片";
    image.loading = "lazy";
    parent.append(image);
    return;
  }
  const element = document.createElement(tag.toLowerCase());
  source.childNodes.forEach((child) => appendRichTextNode(element, child, safeURL));
  parent.append(element);
}
