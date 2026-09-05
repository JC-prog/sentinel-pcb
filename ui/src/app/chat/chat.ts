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

@Component({
  imports: [CommonModule, FormsModule],
  selector: 'app-chat',
  styleUrl: './chat.css',
  templateUrl: './chat.html',
})
export class Chat {
  @ViewChild('scrollAnchor') private scrollAnchor?: ElementRef<HTMLElement>;
  @ViewChild('fileInput') private fileInput?: ElementRef<HTMLInputElement>;

  protected readonly draftText = signal('');
  protected readonly pendingImages = signal<PendingImage[]>([]);

  private readonly conversationId: Signal<string | null>;
  protected readonly conversation: Signal<Conversation | undefined>;
  protected readonly isLoading: Signal<boolean>;

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

    effect(() => {
      this.conversation();
      this.isLoading();
      queueMicrotask(() => this.scrollAnchor?.nativeElement.scrollIntoView?.({ block: 'end' }));
    });
  }

  onFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    const additions = files
      .filter((file) => file.type.startsWith('image/'))
      .map((file) => ({ file, previewUrl: URL.createObjectURL(file) }));
    this.pendingImages.update((images) => [...images, ...additions]);
    input.value = '';
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
    if (!text && images.length === 0) {
      return;
    }

    const conversationId = this.chatService.send(
      this.conversationId(),
      text,
      images.map((image) => image.file),
    );

    this.draftText.set('');
    this.pendingImages.set([]);
    if (this.fileInput) {
      this.fileInput.nativeElement.value = '';
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
