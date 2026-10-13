import {
  Component,
  ElementRef,
  OnDestroy,
  computed,
  effect,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { map } from 'rxjs';
import { CasesClient } from './cases-client';
import { CaseDetail, CaseQuery, CaseSummary } from './cases.models';

const PAGE = 25;

const STATUS_LABELS: Record<string, string> = {
  accepted: 'Accepted',
  review_required: 'Review required',
  approved: 'Approved',
  overridden: 'Overridden',
};

const STATUS_CLASSES: Record<string, string> = {
  accepted: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200',
  review_required: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200',
  approved: 'bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200',
  overridden: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
};

/** The Cases tab: every case chat has saved, readable outside the conversation that made it. Read
 * only on purpose - a case is created and corrected in chat, where the two-step confirm lives. The
 * open case is in the URL (`?case=CASE-000123`) so the Models tab and drift reports can link here. */
@Component({
  imports: [RouterLink],
  selector: 'app-cases',
  styleUrl: './cases.css',
  templateUrl: './cases.html',
})
export class Cases implements OnDestroy {
  private readonly client = inject(CasesClient);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly cases = signal<CaseSummary[]>([]);
  protected readonly total = signal(0);
  protected readonly loading = signal(false);
  protected readonly loadError = signal<string | null>(null);

  protected readonly q = signal('');
  protected readonly status = signal('');
  protected readonly model = signal('');
  protected readonly corrected = signal<'' | 'yes' | 'no'>('');
  protected readonly offset = signal(0);

  protected readonly detail = signal<CaseDetail | null>(null);
  protected readonly detailError = signal<string | null>(null);
  protected readonly detailLoading = signal(false);
  protected readonly imageUrl = signal<string | null>(null);
  protected readonly imageMissing = signal(false);

  /** The detail pane; on a narrow screen it sits below the list, so opening a case scrolls to it. */
  private readonly detailPane = viewChild<ElementRef<HTMLElement>>('detailPane');
  protected readonly copied = signal(false);

  protected readonly selectedNumber = toSignal(
    this.route.queryParamMap.pipe(map((params) => params.get('case'))),
    { initialValue: null },
  );

  protected readonly statuses = Object.entries(STATUS_LABELS);
  protected readonly models = ['pcb_region', 'pcb_body_defect', 'pcb_lead_defect', 'pcb_text_defect'];
  protected readonly page = PAGE;
  protected readonly pageStart = computed(() => (this.total() === 0 ? 0 : this.offset() + 1));
  protected readonly pageEnd = computed(() => Math.min(this.offset() + PAGE, this.total()));
  private request = 0;
  private detailRequest = 0;

  constructor() {
    void this.refresh();

    // The URL is the source of truth for which case is open.
    effect(() => {
      const number = this.selectedNumber();
      untracked(() => void this.loadDetail(number));
    });
  }

  ngOnDestroy(): void {
    this.revokeImage();
  }

  statusLabel(status: string): string {
    return STATUS_LABELS[status] ?? status;
  }

  statusClass(status: string): string {
    return STATUS_CLASSES[status] ?? STATUS_CLASSES['overridden'];
  }

  /** "12 Oct 2026, 14:05" in the viewer's time zone; the raw ISO string if it will not parse. */
  formatDate(iso: string | null): string {
    if (!iso) {
      return '-';
    }
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return iso;
    }
    return date.toLocaleString(undefined, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  async copyNumber(caseNumber: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(caseNumber);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 1500);
    } catch {
      // clipboard blocked (insecure origin, permissions): the number is on screen to select
    }
  }

  percent(value: number | null): string {
    return value === null ? '-' : `${Math.round(value * 100)}%`;
  }

  scores(detail: CaseDetail): { label: string; value: number }[] {
    return Object.entries(detail.defect_scores)
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value);
  }

  filtersChanged(): void {
    this.offset.set(0);
    void this.refresh();
  }

  clearFilters(): void {
    this.q.set('');
    this.status.set('');
    this.model.set('');
    this.corrected.set('');
    this.filtersChanged();
  }

  hasFilters(): boolean {
    return !!(this.q() || this.status() || this.model() || this.corrected());
  }

  next(): void {
    if (this.offset() + PAGE < this.total()) {
      this.offset.update((o) => o + PAGE);
      void this.refresh();
    }
  }

  previous(): void {
    if (this.offset() > 0) {
      this.offset.update((o) => Math.max(0, o - PAGE));
      void this.refresh();
    }
  }

  open(caseNumber: string): void {
    void this.router.navigate([], { queryParams: { case: caseNumber } });
  }

  close(): void {
    void this.router.navigate([], { queryParams: {} });
  }

  async refresh(): Promise<void> {
    const request = ++this.request;
    this.loading.set(true);
    const query: CaseQuery = {
      q: this.q(),
      status: this.status(),
      model: this.model(),
      corrected: this.corrected(),
      offset: this.offset(),
      limit: PAGE,
    };
    try {
      const list = await this.client.list(query);
      if (request === this.request) {
        this.cases.set(list.cases);
        this.total.set(list.total);
        this.loadError.set(null);
      }
    } catch (error) {
      if (request === this.request) {
        this.loadError.set(error instanceof Error ? error.message : 'Could not load the cases.');
      }
    } finally {
      if (request === this.request) {
        this.loading.set(false);
      }
    }
  }

  private async loadDetail(caseNumber: string | null): Promise<void> {
    const request = ++this.detailRequest;
    this.revokeImage();
    this.imageMissing.set(false);
    this.detailError.set(null);
    if (!caseNumber) {
      this.detail.set(null);
      return;
    }
    this.detailLoading.set(true);
    try {
      const detail = await this.client.detail(caseNumber);
      if (request !== this.detailRequest) {
        return;
      }
      this.detail.set(detail);
      // Below the lg breakpoint the pane is under the list, off screen - bring it into view.
      setTimeout(() => this.detailPane()?.nativeElement.scrollIntoView?.({ block: 'start' }), 0);
      try {
        const url = await this.client.imageUrl(detail.image_id);
        if (request !== this.detailRequest) {
          if (url) {
            URL.revokeObjectURL(url);
          }
          return;
        }
        this.imageUrl.set(url);
        this.imageMissing.set(url === null);
      } catch {
        this.imageMissing.set(true);
      }
    } catch (error) {
      if (request === this.detailRequest) {
        this.detail.set(null);
        this.detailError.set(error instanceof Error ? error.message : 'Could not load the case.');
      }
    } finally {
      if (request === this.detailRequest) {
        this.detailLoading.set(false);
      }
    }
  }

  private revokeImage(): void {
    const url = this.imageUrl();
    if (url) {
      URL.revokeObjectURL(url);
    }
    this.imageUrl.set(null);
  }
}
