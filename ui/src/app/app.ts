import { Component, Signal, computed } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { Settings } from './settings/settings';
import { SettingsService } from './settings.service';
import { Sidebar } from './sidebar/sidebar';

const ROUTES_WITHOUT_SIDEBAR = new Set(['/login', '/register']);

@Component({
  imports: [RouterOutlet, Sidebar, Settings],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  protected readonly showSidebar: Signal<boolean>;

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
  }
}
