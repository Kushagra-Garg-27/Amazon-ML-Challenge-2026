// Offline MD -> print-ready HTML builder for the master architecture doc.
// Uses locally-available marked (v18). Mermaid fences are rendered as styled,
// boxed source blocks: no standalone mermaid renderer / mmdc / network is
// available on this machine, and a clean source block beats an empty diagram box.
import { readFileSync, writeFileSync } from 'node:fs';
import { marked } from 'file:///C:/Users/kusha/AppData/Roaming/npm/node_modules/omniroute/node_modules/marked/lib/marked.esm.js';

const SRC = process.argv[2];
const OUT = process.argv[3];
const TITLE = process.argv[4] || 'Architecture';

const esc = (s) => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

let dcount = 0;
marked.use({
  gfm: true,
  breaks: false,
  renderer: {
    code(token) {
      const lang = (token.lang || '').trim();
      const text = token.text || '';
      if (lang === 'mermaid') {
        dcount += 1;
        return `<figure class="diagram"><figcaption>Diagram source ${dcount} (Mermaid — render via mermaid-cli for graphics)</figcaption><pre class="mermaid-src">${esc(text)}</pre></figure>`;
      }
      return `<pre class="code"><code>${esc(text)}</code></pre>`;
    }
  }
});

const md = readFileSync(SRC, 'utf8');
const body = marked.parse(md);

const CSS = `
  @page { size: A4; margin: 16mm 14mm; }
  * { box-sizing: border-box; }
  body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10.2pt; line-height: 1.45; color: #1a1a1a; max-width: 100%; }
  h1 { font-size: 21pt; border-bottom: 3px solid #232f3e; padding-bottom: 6px; color: #232f3e; }
  h2 { font-size: 15pt; margin-top: 22px; border-bottom: 1px solid #d5d9dd; padding-bottom: 3px; color: #232f3e; page-break-after: avoid; }
  h3 { font-size: 12pt; color: #37475a; page-break-after: avoid; }
  h1, h2, h3 { break-inside: avoid; }
  p, li { orphans: 3; widows: 3; }
  code { font-family: "Cascadia Code", Consolas, monospace; font-size: 8.8pt; background: #f2f3f5; padding: 1px 4px; border-radius: 3px; }
  pre.code { background: #f7f8fa; border: 1px solid #dfe2e6; border-left: 4px solid #ff9900; border-radius: 4px; padding: 8px 10px; overflow-x: auto; break-inside: avoid; }
  pre.code code { background: none; padding: 0; font-size: 8.4pt; }
  figure.diagram { margin: 12px 0; break-inside: avoid; }
  figure.diagram figcaption { font-size: 8pt; color: #6a737d; font-style: italic; margin-bottom: 3px; }
  pre.mermaid-src { background: #eef3f8; border: 1px solid #c7d4e0; border-left: 4px solid #146eb4; border-radius: 4px; padding: 8px 10px; font-family: "Cascadia Code", Consolas, monospace; font-size: 7.9pt; line-height: 1.35; white-space: pre-wrap; word-break: break-word; }
  table { border-collapse: collapse; width: 100%; font-size: 8.6pt; margin: 10px 0; break-inside: avoid; }
  th, td { border: 1px solid #cfd4da; padding: 4px 7px; text-align: left; vertical-align: top; }
  th { background: #232f3e; color: #fff; font-weight: 600; }
  tr:nth-child(even) td { background: #f6f7f9; }
  blockquote { border-left: 4px solid #ff9900; margin: 10px 0; padding: 4px 12px; background: #fffaf0; color: #444; }
  a { color: #146eb4; text-decoration: none; }
  hr { border: none; border-top: 1px solid #d5d9dd; margin: 18px 0; }
  strong { color: #111; }
`;

const html = `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>${esc(TITLE)}</title><style>${CSS}</style></head>
<body>${body}</body></html>`;

writeFileSync(OUT, html, 'utf8');
console.error(`[build] wrote ${OUT} | mermaid source blocks: ${dcount} | html bytes: ${Buffer.byteLength(html)}`);
