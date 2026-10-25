import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiClient, Page } from './api-client';
import {
  ArenaMap,
  ComputeSpawns,
  ComputedSightLines,
  MapHeatmap,
  MapListParams,
  MapUploadInput,
  MapZones,
  RankedCells,
  SightLines,
  SightLinesInput,
  SpawnCells,
  StrongSpots,
  StrongSpotsInput,
  ZoneConfig,
  ZoneConfigInput,
} from './maps.models';

/**
 * `/api/maps/`: list, detail, multipart upload, delete, and every map-editor action (ANG-01c).
 * Write actions resolve to the saved state (the matching GET's `data`).
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

  // --- Editor actions ------------------------------------------------------------------------

  /** The Cell grid for `zoneSize`, overlaid with the confirmed config's edits at that size. */
  zones(id: number, zoneSize: number): Observable<MapZones> {
    return this.api.get<MapZones>(`maps/${id}/zones/`, { zone_size: zoneSize });
  }

  /** The latest confirmed zone config plus the map's Bases. */
  zoneConfig(id: number): Observable<ZoneConfig> {
    return this.api.get<ZoneConfig>(`maps/${id}/zone-config/`);
  }

  /** Save Configuration: a full replace of the confirmed config and the Bases. */
  replaceZoneConfig(id: number, body: ZoneConfigInput): Observable<ZoneConfig> {
    return this.api.put<ZoneConfig>(`maps/${id}/zone-config/`, body);
  }

  sightLines(id: number, zoneSize: number): Observable<SightLines> {
    return this.api.get<SightLines>(`maps/${id}/sight-lines/`, { zone_size: zoneSize });
  }

  /** The first Sight-line batch: replaces the stored Sight lines. */
  replaceSightLines(id: number, body: SightLinesInput): Observable<SightLines> {
    return this.api.put<SightLines>(`maps/${id}/sight-lines/`, body);
  }

  /** Every later Sight-line batch: merged into the stored Sight lines. */
  mergeSightLines(id: number, body: SightLinesInput): Observable<SightLines> {
    return this.api.patch<SightLines>(`maps/${id}/sight-lines/`, body);
  }

  /** The synchronous all-pairs LOS compute; `spawns` sends unsaved Spawn edits to protect. */
  computeSightLines(
    id: number,
    zoneSize: number,
    spawns: ComputeSpawns = {},
  ): Observable<ComputedSightLines> {
    return this.api.post<ComputedSightLines>(`maps/${id}/sight-lines/compute/`, {
      zone_size: zoneSize,
      ...spawns,
    });
  }

  rankedCells(id: number, zoneSize: number): Observable<RankedCells> {
    return this.api.get<RankedCells>(`maps/${id}/ranked-cells/`, { zone_size: zoneSize });
  }

  strongSpots(id: number, zoneSize: number): Observable<StrongSpots> {
    return this.api.get<StrongSpots>(`maps/${id}/strong-spots/`, { zone_size: zoneSize });
  }

  replaceStrongSpots(id: number, body: StrongSpotsInput): Observable<StrongSpots> {
    return this.api.put<StrongSpots>(`maps/${id}/strong-spots/`, body);
  }

  spawnCells(id: number): Observable<SpawnCells> {
    return this.api.get<SpawnCells>(`maps/${id}/spawn-cells/`);
  }

  /** The multi-round Movement heatmap; `teamColor` filters to one Side (omitted: both). */
  heatmap(id: number, zoneSize: number, teamColor?: 'red' | 'blue'): Observable<MapHeatmap> {
    return this.api.get<MapHeatmap>(`maps/${id}/heatmap/`, {
      zone_size: zoneSize,
      team_color: teamColor,
    });
  }
}
