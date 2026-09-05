import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { LlmProvider, SettingsService } from '../settings.service';

@Component({
  imports: [FormsModule],
  selector: 'app-settings',
  styleUrl: './settings.css',
  templateUrl: './settings.html',
})
export class Settings {
  constructor(protected readonly settingsService: SettingsService) {}

  selectProvider(provider: LlmProvider): void {
    this.settingsService.setProvider(provider);
  }

  onApiKeyChange(value: string): void {
    this.settingsService.setOpenaiApiKey(value);
  }
}
