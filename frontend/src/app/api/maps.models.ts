// ArenaMap shapes, snake_case exactly as core/serializers.py and core/forms.py
// send / bind them (no mapping layer). Maps are shared reference data: every
// Account sees, uploads and deletes every map (ADR-0038).
import { ListParams } from './api-client';

/** `ArenaMapSerializer`: list rows, `GET maps/{id}/` and the upload response. */
export interface ArenaMap {
  id: number;
  name: string;
  /** Absolute URL of the uploaded image (null without a request context). */
  image: string | null;
  is_default: boolean;
  img_width: number | null;
  img_height: number | null;
  /** ISO 8601 datetime. */
  created_at: string;
  /** True when the map has a confirmed zone config (usable for simulation). */
  has_confirmed_config: boolean;
}

/** `MapUploadForm` fields, sent as multipart `name` + `image`. */
export interface MapUploadInput {
  /** At most 100 characters. */
  name: string;
  image: File;
}

/** `ArenaMapFilterSet` plus `ordering` (`name`, `created_at`, `id`; `-` descends). */
export interface MapListParams extends ListParams {
  is_default?: boolean;
  has_confirmed_config?: boolean;
  ordering?: string;
}
