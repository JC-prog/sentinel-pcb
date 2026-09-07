import { Component } from '@angular/core';
import { BackendStatusService } from '../backend-status.service';

@Component({
  selector: 'app-backend-status-banner',
  styleUrl: './backend-status-banner.css',
  templateUrl: './backend-status-banner.html',
})
export class BackendStatusBanner {
  constructor(protected readonly backendStatus: BackendStatusService) {}

  retry(): void {
    void this.backendStatus.checkNow();
  }
}
