import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { Icon } from '../icons/icon';
import { parseMarkdown } from './markdown';

/** Renders assistant Markdown through Angular bindings only - see markdown.ts for why. */
@Component({
  imports: [NgTemplateOutlet, Icon],
  selector: 'app-markdown',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ng-template #inlines let-nodes>
      @for (node of nodes; track $index) {
        @switch (node.type) {
          @case ('code') {
            <code class="rounded bg-slate-200 px-1 py-0.5 font-mono text-[0.85em] dark:bg-slate-700">{{
              node.text
            }}</code>
          }
          @case ('link') {
            <a
              [href]="node.href"
              target="_blank"
              rel="noopener noreferrer"
              class="text-blue-600 underline hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
              >{{ node.text }}</a
            >
          }
          @default {
            <span [class.font-semibold]="node.bold" [class.italic]="node.italic">{{
              node.text
            }}</span>
          }
        }
      }
    </ng-template>

    <div class="flex flex-col gap-3">
      @for (block of blocks(); track $index) {
        @switch (block.type) {
          @case ('heading') {
            <p class="font-semibold" [class.text-base]="block.level <= 2">
              <ng-container
                [ngTemplateOutlet]="inlines"
                [ngTemplateOutletContext]="{ $implicit: block.inlines }"
              />
            </p>
          }
          @case ('list') {
            <ul class="flex list-none flex-col gap-1">
              @for (item of block.items; track $index) {
                <li class="flex gap-2" [style.margin-left.rem]="item.depth * 1.25">
                  <span
                    class="w-5 shrink-0 text-right text-slate-500 dark:text-slate-400"
                    aria-hidden="true"
                    >{{ item.marker }}</span
                  >
                  <span class="min-w-0">
                    <ng-container
                      [ngTemplateOutlet]="inlines"
                      [ngTemplateOutletContext]="{ $implicit: item.inlines }"
                    />
                  </span>
                </li>
              }
            </ul>
          }
          @case ('quote') {
            <blockquote
              class="border-l-4 border-slate-300 pl-3 whitespace-pre-line text-slate-600 dark:border-slate-600 dark:text-slate-300"
            >
              <ng-container
                [ngTemplateOutlet]="inlines"
                [ngTemplateOutletContext]="{ $implicit: block.inlines }"
              />
            </blockquote>
          }
          @case ('rule') {
            <hr class="border-slate-200 dark:border-slate-700" />
          }
          @case ('code') {
            <div class="relative">
              <button
                type="button"
                class="absolute top-2 right-2 flex items-center gap-1 rounded-md bg-slate-700/80 px-2 py-1 text-xs text-slate-100 hover:bg-slate-600 focus-visible:outline-2 focus-visible:outline-slate-300"
                [attr.aria-label]="copiedIndex() === $index ? 'Copied' : 'Copy code'"
                (click)="copy(block.text, $index)"
              >
                <app-icon [name]="copiedIndex() === $index ? 'check' : 'copy'" [size]="14" />
                {{ copiedIndex() === $index ? 'Copied' : 'Copy' }}
              </button>
              <pre
                class="overflow-x-auto rounded-lg bg-slate-900 p-3 pt-9 font-mono text-xs text-slate-100 dark:bg-slate-950"
              ><code>{{ block.text }}</code></pre>
            </div>
          }
          @case ('table') {
            <div class="overflow-x-auto">
              <table class="min-w-full border-collapse text-left text-xs">
                <thead>
                  <tr>
                    @for (cell of block.head; track $index) {
                      <th class="border-b border-slate-300 px-2 py-1 font-semibold dark:border-slate-600">
                        <ng-container
                          [ngTemplateOutlet]="inlines"
                          [ngTemplateOutletContext]="{ $implicit: cell }"
                        />
                      </th>
                    }
                  </tr>
                </thead>
                <tbody>
                  @for (row of block.rows; track $index) {
                    <tr>
                      @for (cell of row; track $index) {
                        <td class="border-b border-slate-200 px-2 py-1 dark:border-slate-700">
                          <ng-container
                            [ngTemplateOutlet]="inlines"
                            [ngTemplateOutletContext]="{ $implicit: cell }"
                          />
                        </td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
          @case ('paragraph') {
            <p class="whitespace-pre-line">
              <ng-container
                [ngTemplateOutlet]="inlines"
                [ngTemplateOutletContext]="{ $implicit: block.inlines }"
              />
            </p>
          }
        }
      }
    </div>
  `,
})
export class MarkdownView {
  readonly source = input.required<string>();
  protected readonly blocks = computed(() => parseMarkdown(this.source()));
  /** Index of the code block whose Copy button was just used - shows "Copied" for a moment. */
  protected readonly copiedIndex = signal<number | null>(null);

  protected async copy(text: string, index: number): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return; // clipboard blocked (insecure origin, permission) - leave the button as it was
    }
    this.copiedIndex.set(index);
    setTimeout(() => {
      if (this.copiedIndex() === index) {
        this.copiedIndex.set(null);
      }
    }, 1500);
  }
}
