import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { WorkAlert } from './work-alert';

@Component({
  imports: [WorkAlert],
  template: `<app-work-alert kind="success" spacing="mt-3" role="status">Saved</app-work-alert>`,
})
class Host {}

describe('WorkAlert', () => {
  it('shows its content with the colours of its kind and the spacing it was given', () => {
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const el = fixture.nativeElement.querySelector('app-work-alert') as HTMLElement;

    expect(el.textContent).toContain('Saved');
    expect(el.className).toContain('bg-emerald-50');
    expect(el.className).toContain('mt-3');
    expect(el.getAttribute('role')).toBe('status');
  });
});
