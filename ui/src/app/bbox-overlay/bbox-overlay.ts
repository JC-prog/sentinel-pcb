import { AfterViewInit, Component, ElementRef, Input, OnChanges, ViewChild } from '@angular/core';
import { Detection } from '../workflows.service';

const LABEL_COLORS: Record<string, string> = {
  MountingHole: '#42a5f5',
  ComponentBody: '#ab47bc',
  SolderJoint: '#ffca28',
  Lead: '#66bb6a',
};

/** Draws the source image plus each detection's box + label on a canvas, in original
 * image pixel coordinates - matches the bbox convention `app.agents.multi_modal_inference`
 * already un-pads/un-letterboxes to, so no coordinate transform is needed here. */
@Component({
  selector: 'app-bbox-overlay',
  standalone: true,
  template: `<canvas #canvas class="w-full h-auto rounded-lg border border-slate-800"></canvas>`,
})
export class BboxOverlay implements AfterViewInit, OnChanges {
  @ViewChild('canvas') private readonly canvasRef!: ElementRef<HTMLCanvasElement>;

  @Input() imageUrl: string | null = null;
  @Input() detections: Detection[] = [];

  private viewReady = false;

  ngAfterViewInit(): void {
    this.viewReady = true;
    this.draw();
  }

  ngOnChanges(): void {
    if (this.viewReady) {
      this.draw();
    }
  }

  private draw(): void {
    const canvas = this.canvasRef.nativeElement;
    const ctx = canvas.getContext('2d');
    if (!ctx || !this.imageUrl) {
      return;
    }

    const image = new Image();
    image.onload = () => {
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      ctx.drawImage(image, 0, 0);

      for (const detection of this.detections) {
        const [x1, y1, x2, y2] = detection.bbox;
        const color = LABEL_COLORS[detection.label] ?? '#ffffff';

        ctx.strokeStyle = color;
        ctx.lineWidth = 3;
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

        const label = `${detection.label} ${detection.confidence.toFixed(2)}`;
        ctx.font = '16px sans-serif';
        const textWidth = ctx.measureText(label).width;
        const textY = Math.max(y1 - 6, 14);

        ctx.fillStyle = color;
        ctx.fillRect(x1, textY - 14, textWidth + 6, 18);
        ctx.fillStyle = '#0f1724';
        ctx.fillText(label, x1 + 3, textY);
      }
    };
    image.src = this.imageUrl;
  }
}
