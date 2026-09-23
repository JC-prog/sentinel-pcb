import { Component, Signal, computed } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { BackendStatusBanner } from './backend-status-banner/backend-status-banner';
import { ModeToggle } from './mode-toggle/mode-toggle';
import { Settings } from './chat/settings/settings';
import { SettingsService } from './chat/settings.service';
import { Sidebar } from './chat/sidebar/sidebar';
import { ThemeToggle } from './theme-toggle/theme-toggle';

// Work and Models have their own controls (pickers/run buttons; version/queue actions), not
// conversation history, so they get no chat sidebar - same reasoning as login/register.
const ROUTES_WITHOUT_SIDEBAR = new Set(['/login', '/register', '/work', '/models']);

// The Chat/Work/Models toggle only makes sense once logged in - login/register aren't "modes" to switch
// between, so it stays hidden there (unlike the sidebar, it IS shown on /work and /models - that's
// the whole point of the toggle).
const ROUTES_WITHOUT_MODE_TOGGLE = new Set(['/login', '/register']);

@Component({
  imports: [RouterOutlet, Sidebar, Settings, BackendStatusBanner, ThemeToggle, ModeToggle],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  protected readonly showSidebar: Signal<boolean>;
  protected readonly showModeToggle: Signal<boolean>;

  constructor(
    protected readonly settingsService: SettingsService,
    private readonly router: Router,
  ) {
    const url = toSignal(
      this.router.events.pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        map((event) => event.urlAfterRedirects),
      ),
      { initialValue: this.router.url },
    );
    this.showSidebar = computed(() => !ROUTES_WITHOUT_SIDEBAR.has(url()));
    this.showModeToggle = computed(() => !ROUTES_WITHOUT_MODE_TOGGLE.has(url()));
  }
}
