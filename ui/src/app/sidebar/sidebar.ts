import { Component, Signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { ChatService } from '../chat.service';
import { Conversation } from '../models/chat.models';
import { SettingsService } from '../settings.service';
import { ThemeService } from '../theme.service';

@Component({
  imports: [RouterLink],
  selector: 'app-sidebar',
  styleUrl: './sidebar.css',
  templateUrl: './sidebar.html',
})
export class Sidebar {
  protected readonly conversations: Signal<Conversation[]>;

  constructor(
    private readonly chatService: ChatService,
    private readonly router: Router,
    protected readonly themeService: ThemeService,
    protected readonly settingsService: SettingsService,
  ) {
    this.conversations = this.chatService.list();
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
      this.router.navigateByUrl('/');
    }
  }
}
