import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** A boxed status message: red for an error, green for a success. Put `role="alert"` (errors) or
 * `role="status"` (outcomes) on the element so assistive technology announces it. */
@Component({
  changeDetection: ChangeDetectionStrategy.OnPush,
  selector: 'app-work-alert',
  template: '<ng-content />',
  host: { '[class]': 'classes()' },
})
export class WorkAlert {
  readonly kind = input<'error' | 'success'>('error');
  /** Extra margin utility, e.g. `mt-3`. */
  readonly spacing = input('');

  protected readonly classes = computed(() => {
    const colours =
      this.kind() === 'error'
        ? 'border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200'
        : 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200';
    return `block rounded border px-3 py-2 text-sm ${colours} ${this.spacing()}`.trim();
  });
}
