import { Component, ElementRef, Signal, computed, effect, signal, viewChild } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map } from 'rxjs';
import { AuthService } from '../../auth.service';
import { ChatService } from '../chat.service';
import { Icon } from '../icons/icon';
import { Conversation } from '../models/chat.models';
import { SettingsService } from '../settings.service';
import { WorkService } from '../../work/work.service';
import { WorkflowRunSummary } from '../../work/models/orchestrator.models';

// Work and Models have their own controls (pickers/run buttons; version/queue actions), not
// conversation history, so the sidebar shows on those routes too (app.ts's ROUTES_WITHOUT_SIDEBAR)
// but without its "New chat" button and conversation list - just branding, Settings, and the
// user/logout footer, same reasoning app.ts documents for hiding the sidebar entirely pre-login.
const ROUTES_WITHOUT_CHAT_NAV = new Set(['/work', '/models']);

@Component({
  imports: [RouterLink, Icon],
  selector: 'app-sidebar',
  styleUrl: './sidebar.css',
  templateUrl: './sidebar.html',
})
export class Sidebar {
  protected readonly conversations: Signal<Conversation[]>;
  protected readonly showChatNav: Signal<boolean>;
  /** On the Work tab the sidebar lists stored runs instead of conversations. */
  protected readonly showRuns: Signal<boolean>;
  protected readonly runs: Signal<WorkflowRunSummary[]>;
  protected readonly currentRunId: Signal<string | null>;

  /** What the search box holds; matches titles and any messages this browser has loaded. */
  protected readonly query = signal('');
  protected readonly visibleConversations: Signal<Conversation[]>;
  /** The conversation whose title is being edited, and the text so far. */
  protected readonly editingId = signal<string | null>(null);
  protected readonly editTitle = signal('');
  /** The conversation waiting on "Delete this chat?" - a delete is permanent, server-side too. */
  protected readonly confirmingId = signal<string | null>(null);
  private readonly titleInput = viewChild<ElementRef<HTMLInputElement>>('titleInput');

  constructor(
    private readonly chatService: ChatService,
    private readonly router: Router,
    protected readonly settingsService: SettingsService,
    protected readonly authService: AuthService,
    protected readonly workService: WorkService,
  ) {
    this.conversations = this.chatService.list();

    this.visibleConversations = computed(() => {
      const needle = this.query().trim().toLowerCase();
      const all = this.conversations();
      if (!needle) {
        return all;
      }
      return all.filter(
        (c) =>
          c.title.toLowerCase().includes(needle) ||
          c.messages.some((m) => m.content.toLowerCase().includes(needle)),
      );
    });

    const url = toSignal(
      this.router.events.pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        map((event) => event.urlAfterRedirects),
      ),
      { initialValue: this.router.url },
    );
    this.showChatNav = computed(() => !ROUTES_WITHOUT_CHAT_NAV.has(url()));
    this.showRuns = computed(() => url() === '/work');
    this.runs = this.workService.runs;
    this.currentRunId = computed(() => this.workService.result()?.run_id ?? null);

    // History is read when the Work tab is shown (and again when a run finishes, in the service).
    effect(() => {
      if (this.showRuns()) {
        void this.workService.loadRuns();
      }
    });

    effect(() => {
      const input = this.titleInput();
      if (input) {
        input.nativeElement.focus();
        input.nativeElement.select();
      }
    });
  }

  openRun(runId: string): void {
    void this.workService.openRun(runId);
  }

  /** "12 Oct 14:05" - when the run finished, in the viewer's time zone. */
  runTime(savedAtUtc: string): string {
    const date = new Date(savedAtUtc);
    if (Number.isNaN(date.getTime())) {
      return savedAtUtc;
    }
    return date.toLocaleString(undefined, {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  logout(): void {
    void this.authService.logout();
  }

  isActive(id: string): boolean {
    return this.router.url === `/c/${id}`;
  }

  startRename(conversation: Conversation): void {
    this.confirmingId.set(null);
    this.editTitle.set(conversation.title);
    this.editingId.set(conversation.id);
  }

  commitRename(id: string): void {
    if (this.editingId() !== id) {
      return; // already committed or cancelled - blur follows Enter and Escape
    }
    this.editingId.set(null);
    this.chatService.rename(id, this.editTitle());
  }

  cancelRename(): void {
    this.editingId.set(null);
  }

  askDelete(id: string): void {
    this.editingId.set(null);
    this.confirmingId.set(id);
  }

  cancelDelete(): void {
    this.confirmingId.set(null);
  }

  confirmDelete(id: string): void {
    const wasActive = this.isActive(id);
    this.confirmingId.set(null);
    this.chatService.delete(id);
    if (wasActive) {
      this.router.navigateByUrl('/chat');
    }
  }
}
