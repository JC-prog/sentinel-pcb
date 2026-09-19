import { CommonModule } from '@angular/common';
import { Component, ElementRef, Signal, ViewChild, computed, effect, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { map } from 'rxjs/operators';
import { ChatService } from '../chat.service';
import { Conversation } from '../models/chat.models';

interface PendingImage {
  file: File;
  previewUrl: string;
}

interface PendingXml {
  file: File;
}

interface Suggestion {
  label: string;
  prompt: string;
}

@Component({
  imports: [CommonModule, FormsModule],
  selector: 'app-chat',
  styleUrl: './chat.css',
  templateUrl: './chat.html',
})
export class Chat {
  @ViewChild('scrollAnchor') private scrollAnchor?: ElementRef<HTMLElement>;
  @ViewChild('fileInput') private fileInput?: ElementRef<HTMLInputElement>;
  @ViewChild('xmlInput') private xmlInput?: ElementRef<HTMLInputElement>;

  protected readonly suggestions: Suggestion[] = [
    {
      label: 'Classify an inspection image',
      prompt: 'Classify this AOI inspection image for defects.',
    },
    {
      label: 'Flag an ambiguous defect as a case',
      prompt: "I'm not sure if this is a real defect, can you flag it as a case for review?",
    },
    {
      label: 'Review a flagged case',
      prompt: 'Review case CASE-000123.',
    },
    {
      label: 'List cases awaiting review',
      prompt: 'List cases that need review.',
    },
  ];

  protected readonly draftText = signal('');
  protected readonly pendingImages = signal<PendingImage[]>([]);
  /** Inspection XML(s), for create_case (app/agents/case_agent/) - optional, at most what the
   * user explicitly attaches via this separate picker; never inferred from a dropped/pasted
   * image the way pendingImages is. */
  protected readonly pendingXmlFiles = signal<PendingXml[]>([]);
  protected readonly isDraggingOver = signal(false);
  /** dragenter/dragleave fire once per element boundary crossed, including children of the
   * drop zone - a single dragleave doesn't mean the pointer truly left it. Counting enter/leave
   * pairs and only clearing state at zero avoids the overlay flickering off while dragging over
   * a child element. */
  private dragDepth = 0;

  private readonly conversationId: Signal<string | null>;
  protected readonly conversation: Signal<Conversation | undefined>;
  protected readonly isLoading: Signal<boolean>;
  protected readonly toolCallLabel: Signal<string | null>;

  constructor(
    private readonly route: ActivatedRoute,
    private readonly router: Router,
    private readonly chatService: ChatService,
  ) {
    this.conversationId = toSignal(
      this.route.paramMap.pipe(map((params) => params.get('id'))),
      { initialValue: null },
    );

    this.conversation = computed(() => {
      const id = this.conversationId();
      return id ? this.chatService.get(id)() : undefined;
    });

    this.isLoading = computed(() => {
      const id = this.conversationId();
      return id ? this.chatService.isLoading(id)() : false;
    });

    this.toolCallLabel = computed(() => {
      const id = this.conversationId();
      return id ? this.chatService.toolCallLabel(id)() : null;
    });

    effect(() => {
      const id = this.conversationId();
      if (id) {
        this.chatService.ensureLoaded(id);
      }
    });

    effect(() => {
      this.conversation();
      this.isLoading();
      queueMicrotask(() => this.scrollAnchor?.nativeElement.scrollIntoView?.({ block: 'end' }));
    });
  }

  onFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.addFiles(Array.from(input.files ?? []));
    input.value = '';
  }

  onXmlFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const additions = Array.from(input.files ?? [])
      .filter((file) => file.name.toLowerCase().endsWith('.xml'))
      .map((file) => ({ file }));
    this.pendingXmlFiles.update((files) => [...files, ...additions]);
    input.value = '';
  }

  removePendingXml(index: number): void {
    this.pendingXmlFiles.update((files) => files.filter((_, i) => i !== index));
  }

  onDragEnter(event: DragEvent): void {
    event.preventDefault();
    this.dragDepth++;
    this.isDraggingOver.set(true);
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault(); // required for a drop event to fire at all
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.dragDepth = Math.max(0, this.dragDepth - 1);
    if (this.dragDepth === 0) {
      this.isDraggingOver.set(false);
    }
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragDepth = 0;
    this.isDraggingOver.set(false);
    this.addFiles(Array.from(event.dataTransfer?.files ?? []));
  }

  private addFiles(files: File[]): void {
    const additions = files
      .filter((file) => file.type.startsWith('image/'))
      .map((file) => ({ file, previewUrl: URL.createObjectURL(file) }));
    this.pendingImages.update((images) => [...images, ...additions]);
  }

  useSuggestion(suggestion: Suggestion): void {
    this.draftText.set(suggestion.prompt);
  }

  removePendingImage(index: number): void {
    this.pendingImages.update((images) => {
      URL.revokeObjectURL(images[index].previewUrl);
      return images.filter((_, i) => i !== index);
    });
  }

  send(): void {
    const text = this.draftText().trim();
    const images = this.pendingImages();
    const xmlFiles = this.pendingXmlFiles();
    if (!text && images.length === 0 && xmlFiles.length === 0) {
      return;
    }

    const conversationId = this.chatService.send(
      this.conversationId(),
      text,
      images.map((image) => image.file),
      xmlFiles.map((xml) => xml.file),
    );

    this.draftText.set('');
    this.pendingImages.set([]);
    this.pendingXmlFiles.set([]);
    if (this.fileInput) {
      this.fileInput.nativeElement.value = '';
    }
    if (this.xmlInput) {
      this.xmlInput.nativeElement.value = '';
    }

    if (this.conversationId() !== conversationId) {
      this.router.navigate(['/c', conversationId]);
    }
  }

  onTextareaKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }
}
