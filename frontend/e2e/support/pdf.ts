/**
 * Minimal PDF writer for test fixtures: wrapped Helvetica paragraphs per page, no dependencies.
 * Output is plain PDF 1.4 that pypdf (the backend's parser) extracts text from reliably.
 */

const WRAP_AT = 88;

function wrap(paragraph: string): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of paragraph.split(/\s+/).filter(Boolean)) {
    if (line && line.length + word.length + 1 > WRAP_AT) {
      lines.push(line);
      line = word;
    } else {
      line = line ? `${line} ${word}` : word;
    }
  }
  if (line) lines.push(line);
  return lines;
}

/** PDF string literal: ASCII only (standard fonts), with \ ( ) escaped. */
function literal(text: string): string {
  const ascii = text
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[–—]/g, "-")
    .replace(/[^\x20-\x7e]/g, "");
  return `(${ascii.replace(/([\\()])/g, "\\$1")})`;
}

function pageStream(paragraphs: string[]): string {
  const ops = ["BT", "/F1 11 Tf", "72 720 Td", "15 TL"];
  for (const p of paragraphs) {
    for (const l of wrap(p)) ops.push(`${literal(l)} Tj`, "T*");
    ops.push("T*");
  }
  ops.push("ET");
  return ops.join("\n");
}

export interface PdfPage {
  paragraphs: string[];
}

export function makePdf(pages: PdfPage[], { title = "Fixture" } = {}): Buffer {
  const objects: string[] = [];
  const add = (body: string): number => objects.push(body); // new length = 1-based object id

  const catalog = add(""); // placeholders, filled in once page ids are known
  const pagesObj = add("");
  const font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>");
  const info = add(`<< /Title ${literal(title)} /Producer (Scholaria e2e) >>`);

  const kids: number[] = [];
  for (const page of pages) {
    const stream = pageStream(page.paragraphs);
    const contents = add(`<< /Length ${Buffer.byteLength(stream, "latin1")} >>\nstream\n${stream}\nendstream`);
    kids.push(
      add(
        `<< /Type /Page /Parent ${pagesObj} 0 R /MediaBox [0 0 612 792] ` +
          `/Resources << /Font << /F1 ${font} 0 R >> >> /Contents ${contents} 0 R >>`,
      ),
    );
  }
  objects[catalog - 1] = `<< /Type /Catalog /Pages ${pagesObj} 0 R >>`;
  objects[pagesObj - 1] = `<< /Type /Pages /Kids [${kids.map((k) => `${k} 0 R`).join(" ")}] /Count ${kids.length} >>`;

  let out = "%PDF-1.4\n%\xe2\xe3\xcf\xd3\n";
  const offsets: number[] = [];
  objects.forEach((body, i) => {
    offsets.push(Buffer.byteLength(out, "latin1"));
    out += `${i + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = Buffer.byteLength(out, "latin1");
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  out += offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  out += `trailer\n<< /Size ${objects.length + 1} /Root ${catalog} 0 R /Info ${info} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}
