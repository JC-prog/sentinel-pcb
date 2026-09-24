import { Component, Signal, computed } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map } from 'rxjs';
import { AuthService } from '../../auth.service';
import { ChatService } from '../chat.service';
import { Conversation } from '../models/chat.models';
import { SettingsService } from '../settings.service';

// Work and Models have their own controls (pickers/run buttons; version/queue actions), not
// conversation history, so the sidebar shows on those routes too (app.ts's ROUTES_WITHOUT_SIDEBAR)
// but without its "New chat" button and conversation list - just branding, Settings, and the
// user/logout footer, same reasoning app.ts documents for hiding the sidebar entirely pre-login.
const ROUTES_WITHOUT_CHAT_NAV = new Set(['/work', '/models']);

@Component({
  imports: [RouterLink],
  selector: 'app-sidebar',
  styleUrl: './sidebar.css',
  templateUrl: './sidebar.html',
})
export class Sidebar {
  protected readonly conversations: Signal<Conversation[]>;
  protected readonly showChatNav: Signal<boolean>;

  constructor(
    private readonly chatService: ChatService,
    private readonly router: Router,
    protected readonly settingsService: SettingsService,
    protected readonly authService: AuthService,
  ) {
    this.conversations = this.chatService.list();

    const url = toSignal(
      this.router.events.pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        map((event) => event.urlAfterRedirects),
      ),
      { initialValue: this.router.url },
    );
    this.showChatNav = computed(() => !ROUTES_WITHOUT_CHAT_NAV.has(url()));
  }

  logout(): void {
    void this.authService.logout();
  }

  isActive(id: string): boolean {
    return this.router.url === `/c/${id}`;
  }

  deleteConversation(event: Event, id: string): void {
    event.preventDefault();
    event.stopPropagation();
    const wasActive = this.isActive(id);
    this.chatService.delete(id);
    if (wasActive) {
      this.router.navigateByUrl('/chat');
    }
  }
}
