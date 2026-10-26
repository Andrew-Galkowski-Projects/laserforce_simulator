import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient } from './api-client';
import {
  BatchRunInput,
  BatchRunStarted,
  BatchRunStatus,
  SaveGamesInput,
  SaveGamesStatus,
} from './batch.models';

/**
 * `/api/simulate-batch/`: start a Batch run job and poll it, then save its Average or Highlight
 * games through a Save-games job and poll that.
 */
@Injectable({ providedIn: 'root' })
export class BatchService {
  private readonly api = inject(ApiClient);

  start(input: BatchRunInput): Observable<BatchRunStarted> {
    return this.api.post<BatchRunStarted>('simulate-batch/', input);
  }

  /** The optional team / map query params only echo back, so none are sent. */
  status(jobId: string): Observable<BatchRunStatus> {
    return this.api.get<BatchRunStatus>(`simulate-batch/${jobId}/`);
  }

  /** 409 `job_not_complete` while the Batch run job is not complete (or has expired). */
  saveGames(jobId: string, input: SaveGamesInput): Observable<{ job_id: string }> {
    return this.api.post<{ job_id: string }>(`simulate-batch/${jobId}/save/`, input);
  }

  saveStatus(saveJobId: string): Observable<SaveGamesStatus> {
    return this.api.get<SaveGamesStatus>(`simulate-batch/save/${saveJobId}/`);
  }
}
