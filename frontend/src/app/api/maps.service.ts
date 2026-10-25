import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page } from './api-client';
import { ArenaMap, MapListParams, MapUploadInput } from './maps.models';

/**
 * `/api/maps/`: list, detail, multipart upload and delete. Editor actions
 * (zones, sight lines, strong spots, heatmap) are not here.
 */
@Injectable({ providedIn: 'root' })
export class MapsService {
  private readonly api = inject(ApiClient);

  list(params?: MapListParams): Observable<Page<ArenaMap>> {
    return this.api.list<ArenaMap>('maps/', params);
  }

  get(id: number): Observable<ArenaMap> {
    return this.api.get<ArenaMap>(`maps/${id}/`);
  }

  /** The B&W processed image (a raw PNG, not enveloped), e.g. under the Playback map. */
  processedImage(id: number): Observable<Blob> {
    return this.api.getRaw(`maps/${id}/processed-image/`);
  }

  /** The original uploaded image (raw PNG / JPEG, not enveloped): the map list thumbnail. */
  image(id: number): Observable<Blob> {
    return this.api.getRaw(`maps/${id}/image/`);
  }

  /** Multipart POST of `name` + `image`; resolves to the new map (201). */
  upload(body: MapUploadInput): Observable<ArenaMap> {
    const formData = new FormData();
    formData.append('name', body.name);
    formData.append('image', body.image, body.image.name);
    return this.api.upload<ArenaMap>('maps/', formData);
  }

  /** 204; a map a non-completed Season uses is refused with 409 `map_in_use`. */
  delete(id: number): Observable<void> {
    return this.api.delete(`maps/${id}/`);
  }
}
