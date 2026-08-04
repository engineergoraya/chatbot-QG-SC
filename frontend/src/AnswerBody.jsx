/**
 * AnswerBody — renders an assistant answer as real formatted markup.
 *
 * The backend emits a deliberately small, known markdown subset (see
 * ANALYTIC_STRUCTURE / RESPONSE_STYLE in business_rules.py):
 *
 *     **Descriptive**
 *     - lead figure - what it means
 *       - a nested detail
 *     **Diagnostic**
 *     N/A
 *
 * The chat used to print that through `<p>{text}</p>`, so users saw literal
 * asterisks and dashes instead of headings and bullets. This renders it
 * properly.
 *
 * Written by hand rather than pulling in react-markdown: the input is a
 * narrow subset we generate ourselves, this needs no install, and it lets
 * the four analysis headings and the N/A placeholder carry their own styling
 * instead of being generic <strong> and <p>.
 */

const INLINE_BOLD = /\*\*(.+?)\*\*/g;

// A line that is nothing but bold text is a section heading ("**Descriptive**").
const HEADING_ONLY = /^\s*\*\*(.+?)\*\*\s*:?\s*$/;
const BULLET = /^(\s*)[-*]\s+(.*)$/;

/** The format's placeholder for "this section has nothing the data supports". */
function isNA(text) {
  return /^\*{0,2}n\/?a\.?\*{0,2}$/i.test(String(text).trim());
}

/** Inline formatting inside a line: **bold** and `code`. */
function renderInline(text, keyPrefix) {
  const nodes = [];
  let last = 0;
  let match;
  INLINE_BOLD.lastIndex = 0;
  while ((match = INLINE_BOLD.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    nodes.push(<strong key={`${keyPrefix}-b${match.index}`}>{match[1]}</strong>);
    last = match.index + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes.length ? nodes : [text];
}

/**
 * Group consecutive bullet lines into lists, honouring one level of
 * indentation so a sub-point renders as a nested list rather than a
 * same-level sibling.
 */
function renderBullets(items, keyPrefix) {
  const out = [];
  let i = 0;
  while (i < items.length) {
    const { indent, content } = items[i];
    const children = [];
    let j = i + 1;
    while (j < items.length && items[j].indent > indent) {
      children.push(items[j]);
      j += 1;
    }
    out.push(
      <li key={`${keyPrefix}-li${i}`}>
        {renderInline(content, `${keyPrefix}-li${i}`)}
        {children.length > 0 && (
          <ul className="answer-list">{renderBullets(children, `${keyPrefix}-n${i}`)}</ul>
        )}
      </li>
    );
    i = j;
  }
  return out;
}

export default function AnswerBody({ text }) {
  if (!text) return null;

  const lines = String(text).replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let bullets = [];
  let paragraph = [];

  const flushBullets = () => {
    if (!bullets.length) return;
    const key = `ul${blocks.length}`;
    // The model writes the empty-section placeholder either as a bare line
    // ("N/A") or as a single bullet ("- N/A"). Both mean the same thing, so
    // render both as the muted placeholder rather than letting one of them
    // look like a real finding with a bullet next to it.
    if (bullets.length === 1 && isNA(bullets[0].content)) {
      blocks.push(
        <p className="answer-na" key={key}>
          N/A
        </p>
      );
      bullets = [];
      return;
    }
    blocks.push(
      <ul className="answer-list" key={key}>
        {renderBullets(bullets, key)}
      </ul>
    );
    bullets = [];
  };
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const key = `p${blocks.length}`;
    const body = paragraph.join(" ").trim();
    // A bare "N/A" is the format's placeholder for "the data can't support
    // this section" — mute it so it reads as a deliberate absence rather
    // than an answer.
    const na = isNA(body);
    blocks.push(
      <p className={na ? "answer-na" : "answer-text"} key={key}>
        {na ? "N/A" : renderInline(body, key)}
      </p>
    );
    paragraph = [];
  };

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (!line.trim()) {
      flushBullets();
      flushParagraph();
      continue;
    }

    const heading = line.match(HEADING_ONLY);
    if (heading) {
      flushBullets();
      flushParagraph();
      blocks.push(
        <h4 className="answer-heading" key={`h${blocks.length}`}>
          {heading[1]}
        </h4>
      );
      continue;
    }

    const bullet = line.match(BULLET);
    if (bullet) {
      flushParagraph();
      bullets.push({ indent: bullet[1].length, content: bullet[2] });
      continue;
    }

    flushBullets();
    paragraph.push(line.trim());
  }
  flushBullets();
  flushParagraph();

  return <div className="answer-body">{blocks}</div>;
}
