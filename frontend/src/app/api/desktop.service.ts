import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient } from './api-client';
import { DesktopUpdate } from './desktop.models';

/** The Desktop App's own endpoints (DESK-04); Hosted routes none of them. */
@Injectable({ providedIn: 'root' })
export class DesktopService {
  private readonly api = inject(ApiClient);

  /** `GET accounts/desktop-update/`: a newer build of this build's channel, or null. */
  update(): Observable<DesktopUpdate | null> {
    return this.api.get<DesktopUpdate | null>('accounts/desktop-update/');
  }
}
