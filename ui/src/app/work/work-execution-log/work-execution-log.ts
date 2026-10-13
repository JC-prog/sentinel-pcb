import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  Signal,
  afterRenderEffect,
  computed,
  effect,
  input,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { formatLog, logEntryText, logTime } from '../work-log';
import { WorkService } from '../work.service';

/** How many log lines are drawn at once. The full log is always kept (and downloadable); drawing
 * thousands of rows on every new line is what makes a long bulk run sluggish. */
export const LOG_RENDER_LIMIT = 500;

/** A pane counts as "at the bottom" within this many pixels, so a fractional scroll position
 * doesn't stop it following. */
const LOG_BOTTOM_SLACK_PX = 24;

/** The Work tab's execution log: follows new lines until the operator scrolls up, can be filtered,
 * copied or downloaded, and offers the run's result.json once there is one. Reads the run from
 * WorkService; the page tells it only which run it is and what to name the result file. */
@Component({
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule],
  selector: 'app-work-execution-log',
  templateUrl: './work-execution-log.html',
  // The section inside lays out directly in the page's column.
  host: { class: 'contents' },
})
export class WorkExecutionLog implements OnDestroy {
  readonly runId = input<string | null>(null);

  protected readonly logFilter = signal<'all' | 'steps' | 'errors'>('all');
  protected readonly logFollowing = signal(true);
  /** How long the log was when the operator scrolled away from the bottom; null while following. */
  private readonly logPausedAt = signal<number | null>(null);
  protected readonly logNotice = signal<string | null>(null);
  private logNoticeTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly logPane = viewChild<ElementRef<HTMLElement>>('logPane');
  protected readonly logTime = logTime;
  protected readonly logEntryText = logEntryText;

  protected readonly hasRun: Signal<boolean> = computed(() => this.workService.log().length > 0);

  // One computed builds the drawn slice and the hidden count together. The service appends to its
  // array in place, so the filtered list can be the very same array as before; deriving the slice
  // in a separate step would be skipped as "unchanged" and new lines would never be drawn.
  private readonly view = computed(() => {
    const filter = this.logFilter();
    const entries = this.workService.log();
    // Errors are always worth seeing, so the plan-step view keeps them.
    const filtered =
      filter === 'all'
        ? entries
        : entries.filter((entry) =>
            filter === 'errors' ? entry.kind === 'error' : entry.kind !== 'log',
          );
    return {
      visible: filtered.slice(-LOG_RENDER_LIMIT),
      hidden: Math.max(filtered.length - LOG_RENDER_LIMIT, 0),
    };
  });
  protected readonly visibleLog = computed(() => this.view().visible);
  protected readonly hiddenLogCount: Signal<number> = computed(() => this.view().hidden);
  /** Lines that arrived while the operator was reading further up. */
  protected readonly unseenLogCount: Signal<number> = computed(() => {
    const pausedAt = this.logPausedAt();
    return pausedAt === null ? 0 : Math.max(this.workService.log().length - pausedAt, 0);
  });

  protected readonly resultJson: Signal<string | null> = computed(() => {
    const result = this.workService.result();
    return result === null ? null : JSON.stringify(result, null, 2);
  });

  constructor(protected readonly workService: WorkService) {
    // Keep the newest line in view while following - after render, so the new row has a height.
    afterRenderEffect(() => {
      this.visibleLog();
      const pane = this.logPane()?.nativeElement;
      if (pane && untracked(() => this.logFollowing())) {
        pane.scrollTop = pane.scrollHeight;
      }
    });

    // A cleared log starts following again.
    effect(() => {
      if (this.workService.log().length === 0) {
        untracked(() => {
          this.logFollowing.set(true);
          this.logPausedAt.set(null);
        });
      }
    });
  }

  /** Stops following when the operator scrolls up, and resumes when they reach the bottom again. */
  onLogScroll(): void {
    const pane = this.logPane()?.nativeElement;
    if (!pane) {
      return;
    }
    const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < LOG_BOTTOM_SLACK_PX;
    if (atBottom) {
      this.logFollowing.set(true);
      this.logPausedAt.set(null);
    } else if (this.logFollowing()) {
      this.logFollowing.set(false);
      this.logPausedAt.set(this.workService.log().length);
    }
  }

  jumpToLatestLog(): void {
    this.logFollowing.set(true);
    this.logPausedAt.set(null);
    const pane = this.logPane()?.nativeElement;
    if (pane) {
      pane.scrollTop = pane.scrollHeight;
    }
  }

  setLogFilter(filter: 'all' | 'steps' | 'errors'): void {
    this.logFilter.set(filter);
    this.jumpToLatestLog();
  }

  /** Saves the whole log (not just the lines drawn) as a text file. */
  downloadLog(): void {
    const entries = this.workService.log();
    if (entries.length === 0) {
      return;
    }
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    this.saveFile(
      new Blob([formatLog(entries)], { type: 'text/plain' }),
      `execution-log-${this.runId() ?? 'run'}-${stamp}.txt`,
    );
  }

  async copyLog(): Promise<void> {
    const entries = this.workService.log();
    if (entries.length === 0) {
      return;
    }
    try {
      await navigator.clipboard.writeText(formatLog(entries));
      this.logNotice.set(`Copied ${entries.length} line${entries.length === 1 ? '' : 's'}`);
    } catch {
      this.logNotice.set('Could not copy - use Download log instead');
    }
    if (this.logNoticeTimer !== null) {
      clearTimeout(this.logNoticeTimer);
    }
    this.logNoticeTimer = setTimeout(() => this.logNotice.set(null), 2500);
  }

  downloadResult(): void {
    const json = this.resultJson();
    if (json === null) {
      return;
    }
    this.saveFile(
      new Blob([json], { type: 'application/json' }),
      `result-${this.runId() ?? 'run'}.json`,
    );
  }

  ngOnDestroy(): void {
    if (this.logNoticeTimer !== null) {
      clearTimeout(this.logNoticeTimer);
    }
  }

  private saveFile(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    // Revoking on the same tick races the browser actually starting the download - some
    // browsers haven't read the blob yet, so the save can silently fail or produce a truncated
    // file. Deferring a tick gives the download a chance to begin first.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
