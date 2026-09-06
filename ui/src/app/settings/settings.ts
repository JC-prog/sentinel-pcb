import { Component } from '@angular/core';
import { LlmProvider, SettingsService } from '../settings.service';

@Component({
  imports: [],
  selector: 'app-settings',
  styleUrl: './settings.css',
  templateUrl: './settings.html',
})
export class Settings {
  constructor(protected readonly settingsService: SettingsService) {}

  selectProvider(provider: LlmProvider): void {
    this.settingsService.setProvider(provider);
  }
}
