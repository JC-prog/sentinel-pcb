import { Component, HostListener, input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { WorkSettingsService } from '../work-settings.service';

/** The Work tab's settings modal: policy thresholds and LLM planner options. Whether the server
 * has an OpenAI key is the page's to know, so it is passed in rather than fetched twice. */
@Component({
  imports: [FormsModule],
  selector: 'app-work-settings',
  templateUrl: './work-settings.html',
})
export class WorkSettings {
  readonly llmConfigured = input<boolean | null>(null);

  constructor(protected readonly settings: WorkSettingsService) {}

  @HostListener('document:keydown.escape')
  close(): void {
    this.settings.close();
  }
}
