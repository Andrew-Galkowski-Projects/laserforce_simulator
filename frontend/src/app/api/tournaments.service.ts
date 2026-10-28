import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, ListParams, Page } from './api-client';
import {
  PoolEntry,
  PoolGenerateInput,
  Tournament,
  TournamentCreateInput,
  TournamentDetail,
  TournamentListParams,
} from './tournaments.models';

/**
 * `/api/tournaments/` (ANG-01f): list, detail, create, the participant CSV import and the
 * Player pool (list, add, remove, generate, CSV import). Draw, lock and play are ANG-06-13b's.
 */
@Injectable({ providedIn: 'root' })
export class TournamentsService {
  private readonly api = inject(ApiClient);

  /** Newest first by default (`-id`). In-League Tournaments are listed too. */
  list(params?: TournamentListParams): Observable<Page<Tournament>> {
    return this.api.list<Tournament>('tournaments/', params);
  }

  get(id: number): Observable<TournamentDetail> {
    return this.api.get<TournamentDetail>(`tournaments/${id}/`);
  }

  create(body: TournamentCreateInput): Observable<TournamentDetail> {
    return this.api.post<TournamentDetail>('tournaments/', body);
  }

  /** One page of the Player pool (server page size 20 by default). */
  pool(id: number, params?: ListParams): Observable<Page<PoolEntry>> {
    return this.api.list<PoolEntry>(`tournaments/${id}/pool/`, params);
  }

  /** Adds existing Players; the response holds the new entries. */
  addPoolPlayers(id: number, playerIds: number[]): Observable<PoolEntry[]> {
    return this.api.post<PoolEntry[]>(`tournaments/${id}/pool/players/`, {
      player_ids: playerIds,
    });
  }

  removePoolPlayer(id: number, playerId: number): Observable<void> {
    return this.api.delete(`tournaments/${id}/pool/players/${playerId}/`);
  }

  generatePool(id: number, body: PoolGenerateInput): Observable<PoolEntry[]> {
    return this.api.post<PoolEntry[]>(`tournaments/${id}/pool/generate/`, body);
  }

  /** Pool CSV import (multipart): the file under `csv_file`. */
  importPool(id: number, file: File): Observable<PoolEntry[]> {
    return this.api.upload<PoolEntry[]>(`tournaments/${id}/pool/import/`, csvForm(file));
  }

  /** Participant CSV import (multipart, preset Tournaments): the file under `csv_file`. */
  importParticipants(id: number, file: File): Observable<TournamentDetail> {
    return this.api.upload<TournamentDetail>(
      `tournaments/${id}/import-participants/`,
      csvForm(file),
    );
  }
}

function csvForm(file: File): FormData {
  const form = new FormData();
  form.append('csv_file', file);
  return form;
}
