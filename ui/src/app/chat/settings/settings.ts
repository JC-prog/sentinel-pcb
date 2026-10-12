import { Component, OnInit, signal } from '@angular/core';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../auth.service';
import { SettingsService } from '../settings.service';

@Component({
  imports: [],
  selector: 'app-settings',
  styleUrl: './settings.css',
  templateUrl: './settings.html',
})
export class Settings implements OnInit {
  protected readonly loadError = signal(false);

  constructor(
    protected readonly settingsService: SettingsService,
    private readonly authService: AuthService,
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      const response = await this.authService.fetchWithAuth(
        `${environment.apiBaseUrl}/api/chat/models`,
      );
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      const body: { default: string; models: string[] } = await response.json();
      this.settingsService.setAvailableModels(body.models, body.default);
      this.loadError.set(false);
    } catch {
      this.loadError.set(true);
    }
  }

  /** The model that will be used: the picked one, else the server's default. */
  protected selected(): string | null {
    return this.settingsService.model() ?? this.settingsService.defaultModel();
  }

  protected pick(model: string): void {
    // Picking the default is the same as no choice, so a later change of default is followed.
    this.settingsService.setModel(model === this.settingsService.defaultModel() ? null : model);
  }
}
