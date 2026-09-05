import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Settings } from './settings/settings';
import { SettingsService } from './settings.service';
import { Sidebar } from './sidebar/sidebar';

@Component({
  imports: [RouterOutlet, Sidebar, Settings],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  constructor(protected readonly settingsService: SettingsService) {}
}
