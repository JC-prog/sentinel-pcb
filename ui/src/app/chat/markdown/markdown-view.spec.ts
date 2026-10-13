import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { MarkdownView } from './markdown-view';

describe('MarkdownView', () => {
  function render(source: string) {
    const fixture = TestBed.createComponent(MarkdownView);
    fixture.componentRef.setInput('source', source);
    fixture.detectChanges();
    return fixture;
  }

  it('renders a code block with a Copy button that copies its text', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const fixture = render('```\nprint(1)\n```');
    const el = fixture.nativeElement as HTMLElement;

    const button = el.querySelector<HTMLButtonElement>('button[aria-label="Copy code"]');
    expect(button).toBeTruthy();
    expect(el.querySelector('pre code')?.textContent).toBe('print(1)');

    button!.click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(writeText).toHaveBeenCalledWith('print(1)');
    expect(el.querySelector('button[aria-label="Copied"]')).toBeTruthy();
  });

  it('renders raw HTML in a reply as text, not elements', () => {
    const fixture = render('<img src=x onerror=alert(1)>');
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('img')).toBeNull();
    expect(el.textContent).toContain('<img src=x onerror=alert(1)>');
  });
});
