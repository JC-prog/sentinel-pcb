import { parseInline, parseMarkdown } from './markdown';

describe('parseInline', () => {
  it('marks bold and italic runs', () => {
    expect(parseInline('a **b** *c*')).toEqual([
      { type: 'text', text: 'a ', bold: false, italic: false },
      { type: 'text', text: 'b', bold: true, italic: false },
      { type: 'text', text: ' ', bold: false, italic: false },
      { type: 'text', text: 'c', bold: false, italic: true },
    ]);
  });

  it('parses inline code and keeps its contents literal', () => {
    expect(parseInline('use `**x**` here')).toEqual([
      { type: 'text', text: 'use ', bold: false, italic: false },
      { type: 'code', text: '**x**' },
      { type: 'text', text: ' here', bold: false, italic: false },
    ]);
  });

  it('only links http(s) URLs', () => {
    const [link] = parseInline('[docs](https://example.com/a)');
    expect(link).toEqual({ type: 'link', text: 'docs', href: 'https://example.com/a' });

    const unsafe = parseInline('[x](javascript:alert(1))');
    expect(unsafe.every((node) => node.type === 'text')).toBe(true);
  });

  it('leaves an unmatched ** and snake_case untouched', () => {
    expect(parseInline('half **open')).toEqual([
      { type: 'text', text: 'half **open', bold: false, italic: false },
    ]);
    expect(parseInline('my_file_name')).toEqual([
      { type: 'text', text: 'my_file_name', bold: false, italic: false },
    ]);
  });
});

describe('parseMarkdown', () => {
  it('splits paragraphs on blank lines', () => {
    const blocks = parseMarkdown('one\n\ntwo');
    expect(blocks.map((b) => b.type)).toEqual(['paragraph', 'paragraph']);
  });

  it('parses headings, bullet lists and numbered lists', () => {
    const blocks = parseMarkdown('## Title\n- a\n- b\n\n1. x\n2. y');
    expect(blocks.map((b) => b.type)).toEqual(['heading', 'list', 'list']);
    expect(blocks[1]).toMatchObject({ ordered: false });
    expect((blocks[1] as { items: unknown[] }).items.length).toBe(2);
    expect(blocks[2]).toMatchObject({ ordered: true });
  });

  it('nests list items by indentation and numbers each level', () => {
    const [list] = parseMarkdown('1. first\n   - sub a\n   - sub b\n2. second');
    const items = (list as { items: { depth: number; marker: string }[] }).items;
    expect(items.map((i) => [i.depth, i.marker])).toEqual([
      [0, '1.'],
      [1, '•'],
      [1, '•'],
      [0, '2.'],
    ]);
  });

  it('keeps a numbered list together across blank lines and keeps its start number', () => {
    const blocks = parseMarkdown('3. a\n\n4. b\n\n5. c');
    expect(blocks.length).toBe(1);
    const items = (blocks[0] as { items: { marker: string }[] }).items;
    expect(items.map((i) => i.marker)).toEqual(['3.', '4.', '5.']);
  });

  it('parses blockquotes and horizontal rules', () => {
    const blocks = parseMarkdown('> note this\n> and this\n\n---\n\nafter');
    expect(blocks.map((b) => b.type)).toEqual(['quote', 'rule', 'paragraph']);
  });

  it('parses a fenced code block, including one still streaming', () => {
    expect(parseMarkdown('```py\nprint(1)\n```')).toEqual([
      { type: 'code', lang: 'py', text: 'print(1)' },
    ]);
    expect(parseMarkdown('```\nunfinished')).toEqual([
      { type: 'code', lang: '', text: 'unfinished' },
    ]);
  });

  it('parses a table', () => {
    const [table] = parseMarkdown('| Label | Score |\n| --- | --- |\n| open | 0.9 |');
    expect(table.type).toBe('table');
    const { head, rows } = table as { head: unknown[]; rows: unknown[][] };
    expect(head.length).toBe(2);
    expect(rows.length).toBe(1);
  });

  it('never produces raw HTML - tags stay text', () => {
    const [block] = parseMarkdown('<img src=x onerror=alert(1)>');
    expect(block.type).toBe('paragraph');
    expect(JSON.stringify(block)).toContain('<img src=x onerror=alert(1)>');
  });
});
