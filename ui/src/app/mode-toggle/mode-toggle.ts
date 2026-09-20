import { Component, Signal, computed } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map } from 'rxjs';

/** The ChatGPT-style "Chat" / "Work" pill switch mounted in app.html, above the router-outlet -
 * routes to the Chat page ('/', '/c/:id') or the Work page ('/work'). Chat never calls
 * orchestrator_agent and Work never goes through /api/chat/stream - this toggle is the only place
 * the two are visually adjacent. */
@Component({
  imports: [RouterLink],
  selector: 'app-mode-toggle',
  styleUrl: './mode-toggle.css',
  templateUrl: './mode-toggle.html',
})
export class ModeToggle {
  protected readonly isWork: Signal<boolean>;

  constructor(private readonly router: Router) {
    const url = toSignal(
      this.router.events.pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        map((event) => event.urlAfterRedirects),
      ),
      { initialValue: this.router.url },
    );
    this.isWork = computed(() => url().startsWith('/work'));
  }
}
