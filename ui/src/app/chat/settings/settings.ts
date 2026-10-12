import { Component } from '@angular/core';
import { SettingsService } from '../settings.service';

@Component({
  imports: [],
  selector: 'app-settings',
  styleUrl: './settings.css',
  templateUrl: './settings.html',
})
export class Settings {
  constructor(protected readonly settingsService: SettingsService) {}
}
