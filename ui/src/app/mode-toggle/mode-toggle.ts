import { Component, Signal, computed } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map } from 'rxjs';

interface ModeTab {
  label: string;
  /** Where the tab links to. */
  link: string;
  /** The tab is selected while the current URL starts with this. Chat owns '/', '/chat' and
   * '/c/:id', so it is the fallback when neither of the other tabs matches. */
  prefix: string | null;
}

const TABS: readonly ModeTab[] = [
  { label: 'Chat', link: '/chat', prefix: null },
  { label: 'Work', link: '/work', prefix: '/work' },
  { label: 'Models', link: '/models', prefix: '/models' },
];

/** The ChatGPT-style "Chat" / "Work" / "Models" pill switch mounted in app.html, above the
 * router-outlet - routes to the Chat page ('/', '/c/:id'), the Work page ('/work') or the Models
 * page ('/models'). Chat never calls orchestrator_agent, Work never goes through
 * /api/chat/stream, and Models only talks to /api/models - this toggle is the only place the three
 * are visually adjacent. */
@Component({
  imports: [RouterLink],
  selector: 'app-mode-toggle',
  styleUrl: './mode-toggle.css',
  templateUrl: './mode-toggle.html',
})
export class ModeToggle {
  protected readonly tabs = TABS;
  protected readonly active: Signal<ModeTab>;

  constructor(private readonly router: Router) {
    const url = toSignal(
      this.router.events.pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        map((event) => event.urlAfterRedirects),
      ),
      { initialValue: this.router.url },
    );
    this.active = computed(
      () => TABS.find((tab) => tab.prefix !== null && url().startsWith(tab.prefix)) ?? TABS[0],
    );
  }
}
