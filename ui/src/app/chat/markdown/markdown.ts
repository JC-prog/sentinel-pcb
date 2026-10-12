/**
 * A small, dependency-free Markdown parser for assistant replies. It produces a typed tree that
 * the Markdown component renders with ordinary Angular bindings - never `innerHTML` - so model
 * output cannot inject markup or script. Only http(s) links are recognised.
 *
 * Deliberately a subset: paragraphs, headings, (nested) bullet/numbered lists, fenced code,
 * blockquotes, rules, tables, and inline code/bold/italic/links. Anything else is shown as plain
 * text. It tolerates partial input (an unclosed code fence, a half-typed `**`), because replies
 * are rendered while streaming.
 */

export type InlineNode =
  | { type: 'text'; text: string; bold: boolean; italic: boolean }
  | { type: 'code'; text: string }
  | { type: 'link'; text: string; href: string };

export interface ListItem {
  /** 0 for a top-level item; each level of indentation adds one (capped). */
  depth: number;
  /** The bullet, or the item's number ("3."), already worked out. */
  marker: string;
  inlines: InlineNode[];
}

export type MarkdownBlock =
  | { type: 'paragraph'; inlines: InlineNode[] }
  | { type: 'heading'; level: number; inlines: InlineNode[] }
  | { type: 'list'; ordered: boolean; items: ListItem[] }
  | { type: 'code'; text: string; lang: string }
  | { type: 'quote'; inlines: InlineNode[] }
  | { type: 'rule' }
  | { type: 'table'; head: InlineNode[][]; rows: InlineNode[][][] };

const LINK = /^\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/;
const WORD_CHAR = /[A-Za-z0-9]/;
const MAX_LIST_DEPTH = 4;

export function parseInline(source: string): InlineNode[] {
  const nodes: InlineNode[] = [];
  let bold = false;
  let italic = false;
  let buffer = '';

  const flush = (): void => {
    if (buffer) {
      nodes.push({ type: 'text', text: buffer, bold, italic });
      buffer = '';
    }
  };

  let i = 0;
  while (i < source.length) {
    const char = source[i];
    const previous = i > 0 ? source[i - 1] : '';
    const next = source[i + 1] ?? '';

    if (char === '`') {
      const end = source.indexOf('`', i + 1);
      if (end > i + 1) {
        flush();
        nodes.push({ type: 'code', text: source.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    } else if (char === '[') {
      const match = LINK.exec(source.slice(i));
      if (match) {
        flush();
        nodes.push({ type: 'link', text: match[1], href: match[2] });
        i += match[0].length;
        continue;
      }
    } else if (char === '*' && next === '*') {
      if (bold || source.indexOf('**', i + 2) !== -1) {
        flush();
        bold = !bold;
        i += 2;
        continue;
      }
    } else if (char === '*' || char === '_') {
      // Underscores inside words (snake_case, file_names) are not emphasis.
      const boundaryOk = char === '*' || !WORD_CHAR.test(italic ? next : previous);
      const canOpen = italic || (next !== '' && !/\s/.test(next) && source.indexOf(char, i + 1) !== -1);
      if (boundaryOk && canOpen) {
        flush();
        italic = !italic;
        i += 1;
        continue;
      }
    }

    buffer += char;
    i += 1;
  }
  flush();
  return nodes;
}

const FENCE = /^\s*```\s*([\w+-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^\s*>\s?(.*)$/;
const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function splitRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map((cell) => cell.trim());
}

function isOrderedMarker(marker: string): boolean {
  return /\d/.test(marker);
}

function startsBlock(line: string, following: string | undefined): boolean {
  return (
    FENCE.test(line) ||
    HEADING.test(line) ||
    RULE.test(line) ||
    QUOTE.test(line) ||
    LIST_ITEM.test(line) ||
    (line.includes('|') && following !== undefined && TABLE_SEPARATOR.test(following))
  );
}

function parseList(lines: string[], start: number): { block: MarkdownBlock; next: number } {
  const first = LIST_ITEM.exec(lines[start])!;
  const ordered = isOrderedMarker(first[2]);
  const items: ListItem[] = [];
  const counters: (number | undefined)[] = [];
  let i = start;

  while (i < lines.length) {
    const match = LIST_ITEM.exec(lines[i]);
    if (!match) {
      // One blank line inside a list ("loose" lists, which models write often) doesn't end it, as
      // long as the next item is the same kind of list.
      const following = i + 1 < lines.length ? LIST_ITEM.exec(lines[i + 1]) : null;
      if (lines[i].trim() === '' && following && isOrderedMarker(following[2]) === ordered) {
        i += 1;
        continue;
      }
      break;
    }

    const depth = Math.min(
      Math.floor(match[1].replace(/\t/g, '    ').length / 2),
      MAX_LIST_DEPTH,
    );
    counters.length = depth + 1;
    let marker = '•';
    if (isOrderedMarker(match[2])) {
      const number = (counters[depth] ?? parseInt(match[2], 10) - 1) + 1;
      counters[depth] = number;
      marker = `${number}.`;
    } else {
      counters[depth] = undefined;
    }
    items.push({ depth, marker, inlines: parseInline(match[3]) });
    i += 1;
  }

  return { block: { type: 'list', ordered, items }, next: i };
}

export function parseMarkdown(source: string): MarkdownBlock[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const blocks: MarkdownBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === '') {
      i += 1;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // the closing fence (absent while a reply is still streaming)
      blocks.push({ type: 'code', text: body.join('\n'), lang: fence[1] });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, inlines: parseInline(heading[2]) });
      i += 1;
      continue;
    }

    if (RULE.test(line)) {
      blocks.push({ type: 'rule' });
      i += 1;
      continue;
    }

    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      while (i < lines.length) {
        const match = QUOTE.exec(lines[i]);
        if (!match) {
          break;
        }
        quoted.push(match[1]);
        i += 1;
      }
      blocks.push({ type: 'quote', inlines: parseInline(quoted.join('\n')) });
      continue;
    }

    if (line.includes('|') && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1])) {
      const head = splitRow(line).map(parseInline);
      const rows: InlineNode[][][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim() !== '' && lines[i].includes('|')) {
        rows.push(splitRow(lines[i]).map(parseInline));
        i += 1;
      }
      blocks.push({ type: 'table', head, rows });
      continue;
    }

    if (LIST_ITEM.test(line)) {
      const { block, next } = parseList(lines, i);
      blocks.push(block);
      i = next;
      continue;
    }

    const paragraph: string[] = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() !== '' && !startsBlock(lines[i], lines[i + 1])) {
      paragraph.push(lines[i]);
      i += 1;
    }
    blocks.push({ type: 'paragraph', inlines: parseInline(paragraph.join('\n')) });
  }

  return blocks;
}
