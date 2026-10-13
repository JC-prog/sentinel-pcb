import { ChangeDetectionStrategy, Component, HostListener, computed, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { WorkSettingsService } from '../work-settings.service';

/** The Work tab's settings modal: policy thresholds and LLM planner options. Edits are held as a
 * draft and only applied (and remembered) when the operator presses Save; Cancel, Escape, the
 * backdrop and the close button discard them. Whether the server has an OpenAI key is the page's
 * to know, so it is passed in rather than fetched twice. */
@Component({
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule],
  selector: 'app-work-settings',
  templateUrl: './work-settings.html',
})
export class WorkSettings {
  readonly llmConfigured = input<boolean | null>(null);

  protected readonly featureThreshold = signal<number | null>(null);
  protected readonly defectThreshold = signal<number | null>(null);
  protected readonly useLlm = signal(false);
  protected readonly llmModel = signal('');
  protected readonly llmFallback = signal(true);

  /** A threshold is valid when it is a number from 0 to 1. */
  protected readonly invalid = computed(() => !this.valid(this.featureThreshold()) || !this.valid(this.defectThreshold()));

  constructor(protected readonly settings: WorkSettingsService) {
    this.featureThreshold.set(settings.featureThreshold());
    this.defectThreshold.set(settings.defectThreshold());
    this.useLlm.set(settings.useLlm());
    this.llmModel.set(settings.llmModel());
    this.llmFallback.set(settings.llmFallback());
  }

  @HostListener('document:keydown.escape')
  cancel(): void {
    this.settings.close();
  }

  save(): void {
    if (this.invalid()) {
      return;
    }
    this.settings.setFeatureThreshold(this.featureThreshold());
    this.settings.setDefectThreshold(this.defectThreshold());
    this.settings.setUseLlm(this.useLlm());
    this.settings.setLlmModel(this.llmModel().trim());
    this.settings.setLlmFallback(this.llmFallback());
    this.settings.close();
  }

  private valid(value: number | null): boolean {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
  }
}
